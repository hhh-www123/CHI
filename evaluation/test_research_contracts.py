from __future__ import annotations

import csv
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from evaluation.adapters import parse_model_output, render_prompt
from evaluation.annotation_resolution import krippendorff_alpha_ordinal, resolve_annotations
from evaluation.assemble_scoring_dataset import ScoringAssemblyError, _scope_fields, build_scoring_dataset
from evaluation.model_client import ModelCallError, ModelConfig, invoke_json_prompt
from evaluation.prompt_bundle import DEFAULT_DIRECTORY, load_bundle
from evaluation.prepare_experiment import build_cost_plan
from evaluation.run_contract_pilot import make_session
from evaluation.prepare_blinded_annotations import build_blinded_exports
from evaluation.run_real_experiment import RunnerStateError, ValidatedExperiment, execute_validated_experiment
from evaluation.state_executor import (
    apply_allowlisted_patch,
    apply_d_tool_action,
    apply_guarded_candidates,
    apply_trusted_setup,
    apply_whole_document,
)
from evaluation.task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json


class ResearchContractTests(unittest.TestCase):
    def runner_fixture(self, *, budget_cap=1.0, task_id="self-coherence-budget-L1", method="A"):
        suite = compile_suite(read_json(DEFAULT_SOURCE), read_json(DEFAULT_SCHEMA))
        variant = next(item for item in suite["variants"] if item["taskId"] == task_id)
        model = {
            "key": "fixture",
            "provider": "fixture-provider",
            "baseUrl": "http://127.0.0.1:9/v1",
            "model": "fixture-model",
            "versionDate": "2026-11-03",
            "temperature": 0.2,
            "maxOutputTokens": 256,
            "timeoutSeconds": 5,
            "seedSupport": False,
            "apiKeyEnv": "FIXTURE_API_KEY",
            "inputCostPerMillionUsd": 1.0,
            "outputCostPerMillionUsd": 2.0,
            "dataRetentionNotes": "unit-test fixture; no network",
        }
        run = {"runId": "fixture-run", "method": method, "status": "pending"}
        block = {
            "blockId": "fixture-block",
            "modelKey": "fixture",
            "taskId": variant["taskId"],
            "family": variant["family"],
            "interactionLength": 1,
            "trial": 1,
            "methodOrder": [method],
            "runs": [run],
        }
        manifest = {
            "schemaVersion": "1.0",
            "experimentId": "fixture-experiment",
            "formalExperiment": True,
            "formalReady": True,
            "analysisContract": {
                "id": "fixture-analysis",
                "status": "fixture",
                "specHash": "1" * 64,
                "runtimeStatus": "fixture",
                "runtimeVersions": None,
                "preparationScriptHash": "2" * 64,
                "modelScriptHash": "3" * 64,
                "scoringAssemblyHash": hashlib.sha256((Path(__file__).parent / "assemble_scoring_dataset.py").read_bytes()).hexdigest(),
                "annotationResolutionHash": hashlib.sha256((Path(__file__).parent / "annotation_resolution.py").read_bytes()).hexdigest(),
                "annotationSchemaHash": hashlib.sha256((Path(__file__).parent / "annotations" / "schema.json").read_bytes()).hexdigest(),
                "annotationRubricHash": hashlib.sha256((Path(__file__).parents[1] / "research" / "annotation-rubric.md").read_bytes()).hexdigest(),
            },
            "configuration": {
                "budgetCapUsd": budget_cap,
                "randomizationSeed": 7,
                "promptHash": load_bundle(DEFAULT_DIRECTORY)["hash"],
            },
            "blockCount": 1,
            "runCount": 1,
            "blocks": [block],
        }
        return ValidatedExperiment(
            manifest=manifest,
            manifest_hash=canonical_hash(manifest),
            suite=suite,
            variants={variant["taskId"]: variant},
            models={"fixture": model},
            prompt_directory=DEFAULT_DIRECTORY,
        )

    def test_task_suite_compiles_six_families_and_twenty_four_variants(self):
        compiled = compile_suite(read_json(DEFAULT_SOURCE), read_json(DEFAULT_SCHEMA))
        self.assertEqual(compiled["variantCount"], 24)
        self.assertRegex(compiled["sourceHash"], r"^[a-f0-9]{64}$")
        self.assertRegex(compiled["compiledHash"], r"^[a-f0-9]{64}$")
        self.assertNotEqual(compiled["sourceHash"], compiled["compiledHash"])
        self.assertEqual(
            compiled["compiledHash"],
            compile_suite(read_json(DEFAULT_SOURCE), read_json(DEFAULT_SCHEMA))["compiledHash"],
        )
        self.assertEqual(len({item["family"] for item in compiled["variants"]}), 6)
        self.assertEqual(sorted({item["interactionLength"] for item in compiled["variants"]}), [1, 5, 10, 20])
        self.assertTrue(all(len(item["rounds"]) == item["interactionLength"] for item in compiled["variants"]))
        self.assertTrue(all("candidateTargetCount" in round_item for variant in compiled["variants"] for round_item in variant["rounds"]))
        for variant in compiled["variants"]:
            self.assertTrue(all(round_item["turnGoal"]["testedRound"] == round_item["round"] for round_item in variant["rounds"]))
            if variant["interactionLength"] > 1:
                self.assertTrue(all(not round_item["goalEvents"]["test"] for round_item in variant["rounds"][:-1]))
                self.assertEqual(variant["rounds"][-1]["goalEvents"]["test"], variant["rounds"][-1]["activeGoalIds"])
        self.assertTrue(all(
            goal["retentionEligible"] is (variant["family"] != "goal_update")
            for variant in compiled["variants"] for goal in variant["goals"]
        ))
        plan = build_cost_plan([
            {"key": "m1", "maxOutputTokens": 1000, "outputCostPerMillionUsd": 2.0},
            {"key": "m2", "maxOutputTokens": 1000, "outputCostPerMillionUsd": 3.0},
        ], compiled["variants"], 5, ["A", "B", "C", "D"])
        self.assertEqual(plan["plannedProviderCalls"], 8040)
        self.assertEqual(plan["maximumUniqueAnnotationSamples"], 1230)
        self.assertEqual(plan["minimumDoubleCodedSemanticSamples"], 216)
        self.assertEqual(plan["minimumDoubleCodedCandidateBatches"], 30)
        self.assertEqual(plan["minimumAnnotationAssignments"], 1476)
        self.assertEqual(plan["outputAllocationCostUsd"], 20.1)

    def test_registered_ordinal_agreement_is_one_for_perfect_double_codes(self):
        self.assertEqual(krippendorff_alpha_ordinal([[0, 0], [1, 1], [2, 2], [3, 3]]), 1)

    def test_place_scope_allows_catalog_derived_text_without_counting_it_as_recall(self):
        base = "/entities/activities/a/placeId"
        desired, actual, unauthorized, derived = _scope_fields({
            "expectedBehavior": "edit",
            "status": "applied",
            "allowedPaths": [base],
            "changedPaths": [base, base.replace("placeId", "title"), base.replace("placeId", "description")],
        })
        self.assertEqual(desired, [base])
        self.assertEqual(actual, [base])
        self.assertEqual(unauthorized, [])
        self.assertEqual(len(derived), 2)

    def test_literature_logs_are_structured_and_do_not_claim_systematic_completion(self):
        research = Path(__file__).resolve().parents[1] / "research"
        with (research / "literature-log.csv").open(encoding="utf-8", newline="") as handle:
            records = list(csv.DictReader(handle))
        with (research / "literature-query-log.csv").open(encoding="utf-8", newline="") as handle:
            queries = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(records), 12)
        self.assertEqual(len({item["record_id"] for item in records}), len(records))
        self.assertEqual(len(queries), 5)
        self.assertTrue(all(item["database"] == "OpenAlex" for item in queries))
        self.assertTrue(all(item["screening_rule"] == "top 8 by API relevance" and item["notes"] for item in queries))

    def test_prompt_bundle_is_valid_but_deliberately_unfrozen(self):
        bundle = load_bundle(DEFAULT_DIRECTORY)
        self.assertEqual(bundle["status"], "draft")
        self.assertEqual(len(bundle["hash"]), 64)
        self.assertEqual(set(bundle["files"]), {"common.txt", "A.txt", "B.txt", "C.txt", "D.txt"})

    def test_c_contract_accepts_exact_allowlisted_patch(self):
        path = "/entities/activities/a/description"
        parsed = parse_model_output(json.dumps({"decision": "edit", "message": "只改描述", "patch": [{"op": "replace", "path": path, "value": "休息"}]}), "C", [path])
        self.assertEqual(parsed["kind"], "patch")
        self.assertEqual(parsed["patch"][0]["path"], path)

    def test_contract_rejects_duplicate_keys_and_out_of_scope_paths(self):
        path = "/entities/activities/a/description"
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            parse_model_output('{"decision":"edit","message":"x","patch":[],"patch":[]}', "C", [path])
        with self.assertRaisesRegex(ValueError, "outside"):
            parse_model_output(json.dumps({"decision": "edit", "message": "x", "patch": [{"op": "replace", "path": "/forbidden", "value": "x"}]}), "C", [path])
        with self.assertRaisesRegex(ValueError, "length limit"):
            parse_model_output(json.dumps({"decision": "clarify", "message": "x" * 2001}), "A")

    def test_d_contract_requires_unique_registered_strategies(self):
        path = "/entities/activities/a/description"
        payload = {"decision": "edit", "message": "候选", "candidates": [
            {"strategy": "minimal_change", "patch": [{"op": "replace", "path": path, "value": "a"}]},
            {"strategy": "experience_first", "patch": [{"op": "replace", "path": path, "value": "b"}]},
        ]}
        self.assertEqual(len(parse_model_output(json.dumps(payload), "D", [path])["candidates"]), 2)
        payload["candidates"][1]["strategy"] = "minimal_change"
        with self.assertRaisesRegex(ValueError, "unique"):
            parse_model_output(json.dumps(payload), "D", [path])

    def test_shared_decision_envelope_and_d_tools_are_strict(self):
        clarified = parse_model_output('{"decision":"clarify","message":"请确认是否解除约束"}', "A")
        self.assertEqual((clarified["kind"], clarified["decision"]), ("decision", "clarify"))
        restored = parse_model_output('{"decision":"restore","message":"恢复明确版本","targetVersion":2}', "D")
        self.assertEqual(restored["targetVersion"], 2)
        updated = parse_model_output('{"decision":"update_constraint","message":"更新上限","constraint":{"kind":"max_budget","value":3500,"confirmed":true}}', "D")
        self.assertEqual(updated["constraint"]["value"], 3500)
        with self.assertRaisesRegex(ValueError, "A/B"):
            parse_model_output('{"decision":"restore","message":"x","targetVersion":2}', "A")

    def test_explicit_error_is_not_rewritten_as_success(self):
        result = parse_model_output('{"error":{"code":"CANNOT_COMPLETE","message":"no valid output"}}', "A")
        self.assertEqual(result["kind"], "error")

    def test_prompt_renderer_uses_shared_context_and_method_only_fields(self):
        path = "/entities/activities/a/description"
        prompt = render_prompt(DEFAULT_DIRECTORY, "D", {
            "conversation_history": [{"role": "user", "content": "保留预算"}],
            "current_document": {"schemaVersion": "1.0"},
            "declared_controls": [{"kind": "set_max_budget", "value": 3000}],
            "version_history": [{"version": 1, "summary": "initial"}],
            "catalog": {"places": []},
            "current_instruction": "只改描述",
            "allowed_paths": [path],
            "locks_and_constraints": {"locks": [], "constraints": []},
        })
        self.assertIn("保留预算", prompt)
        self.assertIn(path, prompt)
        self.assertIn("方法 D", prompt)
        self.assertNotIn("{conversation_history}", prompt)

    def test_all_methods_share_identical_fixture_owned_document_ids(self):
        from backend.trip_document import to_trip_document
        documents = [to_trip_document(make_session(method, f"run-{method}", "demo-hangzhou-three-day-v1")) for method in ("A", "B", "C", "D")]
        self.assertTrue(all(document == documents[0] for document in documents[1:]))

    def test_model_client_records_usage_without_returning_secret(self):
        response = MagicMock()
        response.status_code = 200
        response.content = b'{}'
        response.headers = {"x-request-id": "fixture-request"}
        response.json.return_value = {
            "model": "fixture-model-revision",
            "choices": [{"finish_reason": "stop", "message": {"content": "{\"patch\":[]}"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        config = ModelConfig("fixture-provider", "http://127.0.0.1:9/v1", "fixture-model", "inert-test-secret", seed=3)
        with patch("evaluation.model_client.httpx.Client", return_value=client):
            result = invoke_json_prompt("return JSON", config)
        self.assertEqual(result["metadata"]["inputTokens"], 12)
        self.assertEqual(result["metadata"]["providerRequestId"], "fixture-request")
        self.assertNotIn("inert-test-secret", json.dumps(result))

    def test_model_client_never_turns_auth_failure_into_mock_success(self):
        response = MagicMock(status_code=401, content=b'{}', headers={})
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        config = ModelConfig("fixture-provider", "http://localhost:9/v1", "fixture-model", "inert-test-secret")
        with patch("evaluation.model_client.httpx.Client", return_value=client):
            with self.assertRaises(ModelCallError) as raised:
                invoke_json_prompt("return JSON", config)
        self.assertEqual(raised.exception.code, "MODEL_AUTH_FAILED")
        self.assertFalse(raised.exception.retryable)

    def test_formal_runner_persists_raw_result_cost_and_resumes_without_recalling(self):
        experiment = self.runner_fixture()
        calls = []

        def fake_invoke(prompt, config):
            calls.append((prompt, config.model))
            return {
                "text": '{"decision":"clarify","message":"请确认预算口径"}',
                "metadata": {
                    "provider": config.provider,
                    "requestedModel": config.model,
                    "responseModel": "fixture-model-revision",
                    "providerRequestId": "fixture-request",
                    "finishReason": "stop",
                    "latencyMs": 1.25,
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "seed": config.seed,
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "formal-results"
            progress = execute_validated_experiment(
                experiment,
                output,
                invoke_fn=fake_invoke,
                supplied_credentials={"fixture": "inert-test-secret"},
            )
            self.assertEqual(progress["status"], "complete")
            self.assertEqual(progress["completedRunCount"], 1)
            self.assertEqual(len(calls), 1)
            record = json.loads((output / "runs" / "fixture-run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["outcome"], "completed")
            self.assertTrue(record["episode"]["formalExperiment"])
            self.assertFalse(record["episode"]["fixture"])
            self.assertEqual(record["episode"]["rounds"][0]["rawResponse"], '{"decision":"clarify","message":"请确认预算口径"}')
            self.assertEqual(record["callEvents"][0]["rawResponse"], record["episode"]["rounds"][0]["rawResponse"])
            self.assertEqual(record["episode"]["rounds"][0]["expectedBehavior"], "clarify")
            self.assertIn("afterDocument", record["episode"]["rounds"][0])
            self.assertIn("finalDocument", record["episode"])
            self.assertAlmostEqual(record["cost"]["observedCostUsd"], 0.00002)
            self.assertNotIn("inert-test-secret", "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.json")))

            resumed = execute_validated_experiment(
                experiment,
                output,
                resume=True,
                invoke_fn=lambda *_: self.fail("completed run must not be called again"),
                supplied_credentials={},
            )
            self.assertEqual(resumed["status"], "complete")
            self.assertEqual(len(calls), 1)

    def test_formal_runner_budget_guard_blocks_before_network(self):
        experiment = self.runner_fixture(budget_cap=0.000001)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "formal-results"
            progress = execute_validated_experiment(
                experiment,
                output,
                invoke_fn=lambda *_: self.fail("budget guard must run before the client"),
                supplied_credentials={"fixture": "inert-test-secret"},
            )
            self.assertEqual(progress["status"], "budget_stopped")
            self.assertEqual(progress["providerCallCount"], 0)
            self.assertEqual(progress["budgetDebitUsd"], 0)
            record = json.loads((output / "runs" / "fixture-run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["outcome"], "budget_stopped")
            self.assertEqual(record["episode"]["rounds"][0]["error"]["code"], "EXPERIMENT_BUDGET_GUARD")

    def test_formal_runner_requires_acknowledgement_before_retrying_interrupted_call(self):
        experiment = self.runner_fixture()
        response = {
            "text": '{"decision":"clarify","message":"请确认预算口径"}',
            "metadata": {
                "provider": "fixture-provider",
                "requestedModel": "fixture-model",
                "responseModel": "fixture-model",
                "providerRequestId": "fixture-retry",
                "finishReason": "stop",
                "latencyMs": 1,
                "inputTokens": 10,
                "outputTokens": 5,
                "seed": None,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "formal-results"
            with self.assertRaises(KeyboardInterrupt):
                execute_validated_experiment(
                    experiment,
                    output,
                    invoke_fn=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
                    supplied_credentials={"fixture": "inert-test-secret"},
                )
            self.assertTrue((output / "in_progress" / "fixture-run.json").exists())
            with self.assertRaisesRegex(RunnerStateError, "explicit --retry-interrupted"):
                execute_validated_experiment(experiment, output, resume=True, supplied_credentials={})

            progress = execute_validated_experiment(
                experiment,
                output,
                resume=True,
                retry_interrupted=True,
                invoke_fn=lambda *_: deepcopy(response),
                supplied_credentials={"fixture": "inert-test-secret"},
            )
            self.assertEqual(progress["status"], "complete")
            self.assertEqual(progress["archivedInterruptedAttemptCount"], 1)
            self.assertEqual(progress["providerCallCount"], 2)
            self.assertEqual(len(list((output / "interruptions").glob("*.json"))), 1)

    def test_completed_formal_run_exports_separate_blinded_packet_and_private_map(self):
        experiment = self.runner_fixture()
        response = {
            "text": '{"decision":"clarify","message":"请明确确认是否更新预算"}',
            "metadata": {
                "provider": "fixture-provider",
                "requestedModel": "fixture-model",
                "responseModel": "fixture-model",
                "providerRequestId": "fixture-blinding",
                "finishReason": "stop",
                "latencyMs": 1,
                "inputTokens": 10,
                "outputTokens": 5,
                "seed": None,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "formal-results"
            execute_validated_experiment(
                experiment,
                output,
                invoke_fn=lambda *_: deepcopy(response),
                supplied_credentials={"fixture": "inert-test-secret"},
            )
            record = json.loads((output / "runs" / "fixture-run.json").read_text(encoding="utf-8"))
            packet, private_map = build_blinded_exports(
                experiment.manifest,
                experiment.suite,
                [record],
                "unit-test-blinding-key-with-at-least-32-bytes",
            )
            self.assertEqual(packet["sampleCount"], 1)
            sample = packet["samples"][0]
            self.assertEqual(sample["sampleType"], "semantic_round")
            self.assertTrue(sample["requiresHumanJudgment"])
            self.assertTrue(sample["beforeDocument"]["tripId"].startswith("trip-"))
            public_text = json.dumps(packet, ensure_ascii=False)
            self.assertNotIn("fixture-run", public_text)
            self.assertNotIn("fixture-model", public_text)
            self.assertNotIn("rawResponse", public_text)
            self.assertEqual(private_map["records"][0]["runId"], "fixture-run")
            self.assertEqual(private_map["records"][0]["method"], "A")
            self.assertEqual(private_map["packetHash"], canonical_hash(packet))
            self.assertEqual(packet["manifestHash"], canonical_hash(experiment.manifest))
            self.assertEqual(packet["analysisContractHash"], canonical_hash(experiment.manifest["analysisContract"]))
            annotations = {
                "schemaVersion": "1.0",
                "status": "pilot",
                "blinded": True,
                "benchmarkHash": packet["benchmarkHash"],
                "promptHash": packet["promptHash"],
                "records": [{
                    "sampleId": sample["sampleId"],
                    "taskId": sample["taskId"],
                    "round": sample["round"],
                    "annotatorId": "annotator-1",
                    "role": "annotator",
                    "goalLabels": [
                        {"goalId": goal_id, "score": 2, "evidence": "输出明确要求确认且状态未改变。"}
                        for goal_id in sample["expectedLabels"]["goalIds"]
                    ],
                    "staleGoalLabels": [],
                    "candidateLabels": [],
                    "conflictLabel": {"groundTruth": "clarify", "systemDecision": "clarify"},
                    "notes": "unit test",
                }],
            }
            annotation_schema = json.loads((Path(__file__).parent / "annotations" / "schema.json").read_text(encoding="utf-8"))
            resolved, stats = resolve_annotations(packet, private_map, annotations, annotation_schema)
            self.assertEqual(set(resolved[sample["sampleId"]]["goalScores"].values()), {2})
            self.assertEqual(stats["resolvedSampleCount"], 1)
            self.assertFalse(stats["formalReady"])
            dataset = build_scoring_dataset(
                experiment.manifest,
                experiment.suite,
                [record],
                packet,
                private_map,
                resolved,
                stats,
                require_formal_annotations=False,
            )
            self.assertEqual(len(dataset["episodes"]), 1)
            self.assertEqual(dataset["manifestHash"], packet["manifestHash"])
            self.assertEqual(dataset["analysisContractHash"], packet["analysisContractHash"])
            self.assertTrue(dataset["episodes"][0]["success"])
            self.assertEqual(dataset["episodes"][0]["executionPosition"], 1)
            self.assertEqual(dataset["episodes"][0]["rounds"][0]["turnGoal"]["satisfied"], True)
            self.assertEqual(dataset["episodes"][0]["rounds"][0]["activeGoals"][0]["id"], "budget-constraint-evidence")
            tampered_packet = deepcopy(packet)
            tampered_packet["manifestHash"] = "0" * 64
            with self.assertRaisesRegex(ScoringAssemblyError, "packet manifestHash"):
                build_scoring_dataset(
                    experiment.manifest,
                    experiment.suite,
                    [record],
                    tampered_packet,
                    private_map,
                    resolved,
                    stats,
                    require_formal_annotations=False,
                )

    def test_blinded_candidate_batch_hides_strategy_and_merges_relevance(self):
        experiment = self.runner_fixture(task_id="retention-budget-hotel-L1", method="D")

        def candidate_response(prompt, _config):
            match = re.search(r"<allowed_paths>\n([^\n]+)\n</allowed_paths>", prompt)
            self.assertIsNotNone(match)
            path = json.loads(match.group(1))[0]
            candidates = [
                {"strategy": strategy, "patch": [{"op": "replace", "path": path, "value": value}]}
                for strategy, value in (
                    ("minimal_change", "轻松游览并适时休息。"),
                    ("efficiency_first", "缩短停留，保留核心体验。"),
                    ("experience_first", "慢慢体验景点并留出休息时间。"),
                )
            ]
            return {
                "text": json.dumps({"decision": "edit", "message": "提供三个局部方案", "candidates": candidates,}, ensure_ascii=False),
                "metadata": {
                    "provider": "fixture-provider", "requestedModel": "fixture-model", "responseModel": "fixture-model",
                    "providerRequestId": "fixture-candidates", "finishReason": "stop", "latencyMs": 1,
                    "inputTokens": 10, "outputTokens": 20, "seed": None,
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "formal-results"
            execute_validated_experiment(
                experiment,
                output,
                invoke_fn=candidate_response,
                supplied_credentials={"fixture": "inert-test-secret"},
            )
            record = json.loads((output / "runs" / "fixture-run.json").read_text(encoding="utf-8"))
            packet, private_map = build_blinded_exports(
                experiment.manifest,
                experiment.suite,
                [record],
                "unit-test-candidate-blinding-key-at-least-32-bytes",
            )
            self.assertEqual(packet["sampleCount"], 2)
            candidate_sample = next(item for item in packet["samples"] if item["sampleType"] == "candidate_batch")
            self.assertEqual(len(candidate_sample["candidates"]), 3)
            self.assertNotIn("minimal_change", json.dumps(packet, ensure_ascii=False))

            annotation_records = []
            for sample in packet["samples"]:
                annotation_records.append({
                    "sampleId": sample["sampleId"],
                    "taskId": sample["taskId"],
                    "round": sample["round"],
                    "annotatorId": "annotator-1",
                    "role": "annotator",
                    "goalLabels": [
                        {"goalId": goal_id, "score": 2, "evidence": "满足目标且未越界。"}
                        for goal_id in sample["expectedLabels"]["goalIds"]
                    ],
                    "staleGoalLabels": [],
                    "candidateLabels": [
                        {"candidateId": candidate_id, "relevance": 3 - index, "humanBest": index == 0, "evidence": "候选相关性测试。"}
                        for index, candidate_id in enumerate(sample["expectedLabels"]["candidateIds"])
                    ],
                    "conflictLabel": (
                        {"groundTruth": "accept", "systemDecision": "accept"}
                        if sample["sampleType"] == "semantic_round"
                        else {"groundTruth": "not_applicable", "systemDecision": "not_applicable"}
                    ),
                    "notes": "unit test",
                })
            second_semantic = deepcopy(next(
                item for item in annotation_records
                if next(sample for sample in packet["samples"] if sample["sampleId"] == item["sampleId"])["sampleType"] == "semantic_round"
            ))
            second_semantic["annotatorId"] = "annotator-2"
            annotation_records.append(second_semantic)
            annotations = {
                "schemaVersion": "1.0", "status": "pilot", "blinded": True,
                "benchmarkHash": packet["benchmarkHash"], "promptHash": packet["promptHash"],
                "records": annotation_records,
            }
            annotation_schema = json.loads((Path(__file__).parent / "annotations" / "schema.json").read_text(encoding="utf-8"))
            resolved, stats = resolve_annotations(packet, private_map, annotations, annotation_schema)
            self.assertEqual(stats["doubleCodedRate"], 0.5)
            self.assertEqual(stats["doubleCodedSemanticRate"], 1)
            self.assertEqual(stats["doubleCodedCandidateRate"], 0)
            self.assertIn("fewer than 20% of candidate batches are double-coded", stats["blockers"])
            dataset = build_scoring_dataset(
                experiment.manifest, experiment.suite, [record], packet, private_map, resolved, stats,
                require_formal_annotations=False,
            )
            scored_round = dataset["episodes"][0]["rounds"][0]
            self.assertEqual(scored_round["candidateTargetCount"], 3)
            self.assertEqual(sum(item["valid"] for item in scored_round["candidates"]), 3)
            self.assertTrue(dataset["episodes"][0]["success"])

    def test_a_and_c_state_executors_keep_their_registered_guard_difference(self):
        from backend.app import initial_payload
        from backend.trip_document import normalize_editor_state, to_trip_document
        session = normalize_editor_state({
            "id": "a" * 32,
            "version": 1,
            "history": [],
            "_undo": [],
            "_undoVersionIds": [],
            **initial_payload(),
        })
        document = to_trip_document(session)
        activity_id = document["entities"]["days"][document["dayOrder"][0]]["activityIds"][0]
        path = f"/entities/activities/{activity_id}/description"
        whole = deepcopy(document)
        whole["budgetLimit"] = 9999
        applied_a = apply_whole_document(document, {"kind": "document", "document": whole})
        self.assertIn("/budgetLimit", applied_a["changedPaths"])
        self.assertEqual(applied_a["document"]["version"], document["version"] + 1)
        applied_c = apply_allowlisted_patch(document, {"kind": "patch", "patch": [{"op": "replace", "path": path, "value": "局部修改"}]})
        self.assertEqual(applied_c["changedPaths"], [path])
        self.assertEqual(applied_c["document"]["budgetLimit"], document["budgetLimit"])
        self.assertEqual(applied_c["document"]["version"], document["version"] + 1)

    def test_whole_document_executor_overrides_system_identity_but_records_attempt(self):
        from backend.app import initial_payload
        from backend.trip_document import normalize_editor_state, to_trip_document
        session = normalize_editor_state({"id": "e" * 32, "version": 4, "history": [], "_undo": [], "_undoVersionIds": [], **initial_payload()})
        document = to_trip_document(session)
        generated = deepcopy(document)
        generated["tripId"] = "model-replaced-id"
        generated["version"] = 99
        result = apply_whole_document(document, {"kind": "document", "document": generated})
        self.assertEqual(result["document"]["tripId"], document["tripId"])
        self.assertEqual(result["document"]["version"], 5)
        self.assertEqual({item["path"] for item in result["systemIdentityOverrides"]}, {"/tripId", "/version"})
        self.assertIn("/tripId", result["rawModelChangedPaths"])

    def test_d_state_executor_revalidates_and_projects_selected_candidate(self):
        from backend.app import initial_payload
        from backend.trip_document import component_registry, normalize_editor_state, to_trip_document
        session = normalize_editor_state({
            "id": "b" * 32,
            "version": 1,
            "history": [],
            "_undo": [],
            "_undoVersionIds": [],
            **initial_payload(),
        })
        binding = next(item for item in component_registry(session).values() if item["kind"] == "ActivityCard" and item["dayKey"] == "day2" and item["stopId"] == "day2-stop-2")
        path = f"/entities/activities/{binding['entityId']}/description"
        parsed = {"kind": "candidates", "candidates": [
            {"strategy": "minimal_change", "patch": [{"op": "replace", "path": path, "value": "安排休息并缩短停留。"}]},
        ]}
        result = apply_guarded_candidates(
            session,
            parsed,
            selected_component_ids=[binding["componentId"]],
            fields=["description"],
            instruction="安排轻松一些",
            base_version=1,
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["session"]["version"], 2)
        self.assertEqual(result["document"]["entities"]["activities"][binding["entityId"]]["description"], "安排休息并缩短停留。")
        self.assertTrue(all(result["guardReport"].values()))

    def test_trusted_setup_is_declared_for_all_methods_but_enforced_only_for_d(self):
        from backend.app import initial_payload
        from backend.trip_document import normalize_editor_state
        session = normalize_editor_state({"id": "c" * 32, "version": 1, "history": [], "_undo": [], "_undoVersionIds": [], **initial_payload()})
        actions = [{"kind": "set_max_budget", "value": 3000}, {"kind": "lock_day", "dayKey": "day1"}]
        baseline = apply_trusted_setup(session, actions, enforce_guards=False)
        guarded = apply_trusted_setup(session, actions, enforce_guards=True)
        self.assertEqual(baseline["declaredControls"], guarded["declaredControls"])
        self.assertFalse(any(item.get("kind") == "max_budget" for item in baseline["session"]["persistedConstraints"]["hardConstraints"]))
        self.assertTrue(any(item.get("kind") == "max_budget" for item in guarded["session"]["persistedConstraints"]["hardConstraints"]))
        self.assertIn("day-1", guarded["session"]["controlState"]["lockedRegions"])

    def test_d_constraint_tool_updates_persistent_guard(self):
        from backend.app import initial_payload
        from backend.trip_document import normalize_editor_state
        session = normalize_editor_state({"id": "d" * 32, "version": 1, "history": [], "_undo": [], "_undoVersionIds": [], **initial_payload()})
        parsed = parse_model_output('{"decision":"update_constraint","message":"更新","constraint":{"kind":"max_budget","value":3500,"confirmed":true}}', "D")
        result = apply_d_tool_action(session, parsed, version_snapshots={1: deepcopy(session)}, selected_component_ids=[], fields=[], instruction="更新预算")
        self.assertEqual(result["status"], "constraint_updated")
        self.assertTrue(any(item.get("kind") == "max_budget" and item.get("value") == 3500 for item in result["session"]["persistedConstraints"]["hardConstraints"]))


if __name__ == "__main__":
    unittest.main()
