#!/usr/bin/env python3
"""Create method-blinded annotation packets from completed formal run files.

The public packet never contains method/model/run identifiers, raw action
envelopes, candidate strategies, rule scores, or system ranks. A separate
private mapping is required to merge labels after the analysis plan is frozen.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft202012Validator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

try:
    from .prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from .run_real_experiment import (
        SCHEMA_VERSION as RUNNER_SCHEMA_VERSION,
        RunnerStateError,
        _atomic_create_json,
        _read_object,
        _validate_record_hash,
    )
    from .task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json
except ImportError:  # direct script execution
    from prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from run_real_experiment import (
        SCHEMA_VERSION as RUNNER_SCHEMA_VERSION,
        RunnerStateError,
        _atomic_create_json,
        _read_object,
        _validate_record_hash,
    )
    from task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json


PACKET_SCHEMA_VERSION = "blinded-annotations-1.0"
PACKET_SCHEMA = REPOSITORY_ROOT / "evaluation" / "annotations" / "packet-schema.json"
SUCCESSFUL_OUTPUT_STATUSES = {"applied", "clarify", "reject", "restored", "constraint_updated"}


class AnnotationPreparationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blind_digest(key: bytes, namespace: str, value: str, length: int = 20) -> str:
    return hmac.new(key, f"{namespace}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:length]


def _blind_id(key: bytes, namespace: str, value: str) -> str:
    return namespace + "-" + _blind_digest(key, namespace, value)


def _parse_message(raw_response) -> str | None:
    if not isinstance(raw_response, str):
        return None
    try:
        value = json.loads(raw_response)
    except json.JSONDecodeError:
        return None
    message = value.get("message") if isinstance(value, dict) else None
    return message if isinstance(message, str) and message else None


def _system_decision(status: str) -> str:
    if status == "clarify":
        return "clarify"
    if status == "reject":
        return "reject"
    if status in {"applied", "restored", "constraint_updated"}:
        return "accept"
    return "not_applicable"


def _ground_truth_decision(expected: str) -> str:
    if expected == "clarify":
        return "clarify"
    if expected == "reject":
        return "reject"
    return "accept"


class IdentityBlinder:
    """Pseudonymize structural IDs while preserving equality and ID drift."""

    def __init__(self, key: bytes, sample_namespace: str, documents: list[dict]):
        self.key = key
        self.namespace = sample_namespace
        self.days: set[str] = set()
        self.activities: set[str] = set()
        self.components: set[str] = set()
        for document in documents:
            entities = document.get("entities", {}) if isinstance(document, dict) else {}
            days = entities.get("days", {}) if isinstance(entities, dict) else {}
            activities = entities.get("activities", {}) if isinstance(entities, dict) else {}
            if isinstance(days, dict):
                self.days.update(item for item in days if isinstance(item, str))
                for value in days.values():
                    if isinstance(value, dict) and isinstance(value.get("id"), str):
                        self.days.add(value["id"])
            if isinstance(activities, dict):
                self.activities.update(item for item in activities if isinstance(item, str))
                for value in activities.values():
                    if isinstance(value, dict) and isinstance(value.get("id"), str):
                        self.activities.add(value["id"])
            components = document.get("ui", {}).get("components", {}) if isinstance(document, dict) else {}
            if isinstance(components, dict):
                self.components.update(item for item in components if isinstance(item, str))
                for value in components.values():
                    if isinstance(value, dict) and isinstance(value.get("id"), str):
                        self.components.add(value["id"])

    def alias(self, kind: str, value: str) -> str:
        return kind + "-" + _blind_digest(self.key, self.namespace + ":" + kind, value, 16)

    def entity_alias(self, value: str) -> str:
        if value in self.days:
            return self.alias("day", value)
        if value in self.activities:
            return self.alias("activity", value)
        return self.alias("entity", value)

    def document(self, document: dict) -> dict:
        result = deepcopy(document)
        if isinstance(result.get("tripId"), str):
            result["tripId"] = self.alias("trip", result["tripId"])
        if isinstance(result.get("dayOrder"), list):
            result["dayOrder"] = [self.alias("day", item) if isinstance(item, str) else item for item in result["dayOrder"]]
        entities = result.get("entities", {})
        if isinstance(entities, dict):
            days = entities.get("days", {})
            if isinstance(days, dict):
                blinded_days = {}
                for raw_key, day in days.items():
                    item = deepcopy(day)
                    if isinstance(item, dict):
                        if isinstance(item.get("id"), str):
                            item["id"] = self.alias("day", item["id"])
                        if isinstance(item.get("activityIds"), list):
                            item["activityIds"] = [self.alias("activity", value) if isinstance(value, str) else value for value in item["activityIds"]]
                    blinded_days[self.alias("day", raw_key)] = item
                entities["days"] = blinded_days
            activities = entities.get("activities", {})
            if isinstance(activities, dict):
                blinded_activities = {}
                for raw_key, activity in activities.items():
                    item = deepcopy(activity)
                    if isinstance(item, dict):
                        if isinstance(item.get("id"), str):
                            item["id"] = self.alias("activity", item["id"])
                        if isinstance(item.get("dayId"), str):
                            item["dayId"] = self.alias("day", item["dayId"])
                    blinded_activities[self.alias("activity", raw_key)] = item
                entities["activities"] = blinded_activities
        components = result.get("ui", {}).get("components", {})
        if isinstance(components, dict):
            blinded_components = {}
            for raw_key, component in components.items():
                item = deepcopy(component)
                if isinstance(item, dict):
                    if isinstance(item.get("id"), str):
                        item["id"] = self.alias("component", item["id"])
                    if isinstance(item.get("entityId"), str):
                        item["entityId"] = self.entity_alias(item["entityId"])
                    if isinstance(item.get("parentId"), str):
                        item["parentId"] = self.alias("component", item["parentId"])
                blinded_components[self.alias("component", raw_key)] = item
            result["ui"]["components"] = blinded_components
        return result

    def pointer(self, path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/"):
            return path
        tokens = path.split("/")
        if len(tokens) >= 4 and tokens[1:3] == ["entities", "days"]:
            tokens[3] = self.alias("day", tokens[3])
        elif len(tokens) >= 4 and tokens[1:3] == ["entities", "activities"]:
            tokens[3] = self.alias("activity", tokens[3])
        elif len(tokens) >= 4 and tokens[1:3] == ["ui", "components"]:
            tokens[3] = self.alias("component", tokens[3])
        return "/".join(tokens)


def _replace_pointer(document: dict, path: str, value) -> None:
    tokens = path.removeprefix("/").split("/")
    tokens = [item.replace("~1", "/").replace("~0", "~") for item in tokens]
    current = document
    for token in tokens[:-1]:
        if not isinstance(current, dict) or token not in current:
            raise AnnotationPreparationError(f"candidate patch path does not exist: {path}")
        current = current[token]
    if not isinstance(current, dict) or tokens[-1] not in current:
        raise AnnotationPreparationError(f"candidate patch path does not exist: {path}")
    current[tokens[-1]] = deepcopy(value)


def _candidate_documents(before_document: dict, round_record: dict) -> list[dict]:
    accepted = round_record.get("application", {}).get("acceptedCandidates", [])
    if not isinstance(accepted, list):
        raise AnnotationPreparationError("acceptedCandidates must be a list")
    result = []
    for rank, candidate in enumerate(accepted, 1):
        if not isinstance(candidate, dict) or not isinstance(candidate.get("strategy"), str) or not isinstance(candidate.get("patch"), list):
            raise AnnotationPreparationError("accepted candidate record is invalid")
        document = deepcopy(before_document)
        for operation in candidate["patch"]:
            if not isinstance(operation, dict) or operation.get("op") != "replace":
                raise AnnotationPreparationError("only recorded replace candidates can be annotated")
            _replace_pointer(document, operation["path"], operation["value"])
        if isinstance(document.get("version"), int):
            document["version"] += 1
        result.append({
            "strategy": candidate["strategy"],
            "systemRank": rank,
            "document": document,
        })
    return result


def load_completed_formal_results(
    result_directory: Path,
    *,
    tasks_path: Path = DEFAULT_SOURCE,
    schema_path: Path = DEFAULT_SCHEMA,
    prompt_directory: Path = DEFAULT_PROMPTS,
) -> tuple[dict, dict, list[dict]]:
    result_directory = result_directory.resolve()
    snapshot = _read_object(result_directory / "manifest.snapshot.json")
    progress = _read_object(result_directory / "progress.json")
    manifest = snapshot.get("manifest")
    if snapshot.get("schemaVersion") != RUNNER_SCHEMA_VERSION or not isinstance(manifest, dict):
        raise AnnotationPreparationError("result directory has no valid formal runner snapshot")
    manifest_hash = canonical_hash(manifest)
    if snapshot.get("manifestHash") != manifest_hash:
        raise AnnotationPreparationError("manifest snapshot hash mismatch")
    if manifest.get("formalExperiment") is not True or manifest.get("formalReady") is not True:
        raise AnnotationPreparationError("annotation export requires a formal-ready experiment")
    analysis_contract = manifest.get("analysisContract")
    if not isinstance(analysis_contract, dict):
        raise AnnotationPreparationError("formal manifest has no analysis contract")
    current_annotation_hash = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    if analysis_contract.get("annotationPreparationHash") != current_annotation_hash:
        raise AnnotationPreparationError("running annotation exporter does not match the formal analysis contract")
    if analysis_contract.get("annotationPacketSchemaHash") != hashlib.sha256(PACKET_SCHEMA.read_bytes()).hexdigest():
        raise AnnotationPreparationError("annotation packet schema does not match the formal analysis contract")
    rubric_path = REPOSITORY_ROOT / "research" / "annotation-rubric.md"
    if analysis_contract.get("annotationRubricHash") != hashlib.sha256(rubric_path.read_bytes()).hexdigest():
        raise AnnotationPreparationError("annotation rubric does not match the formal analysis contract")
    if progress.get("status") != "complete" or progress.get("manifestHash") != manifest_hash:
        raise AnnotationPreparationError("all scheduled runs must be complete before blinding")

    suite = compile_suite(read_json(tasks_path), read_json(schema_path))
    prompts = load_bundle(prompt_directory)
    if manifest.get("benchmark", {}).get("hash") != suite["sourceHash"]:
        raise AnnotationPreparationError("formal results do not match the selected benchmark")
    if manifest.get("benchmark", {}).get("compiledHash") != suite["compiledHash"]:
        raise AnnotationPreparationError("formal results do not match the selected compiled benchmark")
    if manifest.get("configuration", {}).get("promptHash") != prompts["hash"]:
        raise AnnotationPreparationError("formal results do not match the selected prompt bundle")

    expected = {}
    for block in manifest.get("blocks", []):
        for run in block.get("runs", []):
            expected[run["runId"]] = {
                "blockId": block["blockId"],
                "modelKey": block["modelKey"],
                "taskId": block["taskId"],
                "trial": block["trial"],
                "method": run["method"],
            }
    run_paths = sorted((result_directory / "runs").glob("*.json"))
    if len(run_paths) != manifest.get("runCount") or len(run_paths) != len(expected):
        raise AnnotationPreparationError("completed run file count does not match the manifest")
    records = []
    for path in run_paths:
        record = _read_object(path)
        _validate_record_hash(record, path)
        run_id = record.get("runId")
        if run_id not in expected or path.stem != run_id:
            raise AnnotationPreparationError(f"unexpected run file: {path}")
        if record.get("status") != "completed" or record.get("formalExperiment") is not True or record.get("fixture") is not False or record.get("manifestHash") != manifest_hash:
            raise AnnotationPreparationError(f"run provenance mismatch: {path}")
        for key, value in expected[run_id].items():
            if record.get(key) != value:
                raise AnnotationPreparationError(f"run schedule mismatch: {path}")
        records.append(record)
    return manifest, suite, records


def build_blinded_exports(manifest: dict, suite: dict, records: list[dict], blinding_key: str) -> tuple[dict, dict]:
    if not isinstance(blinding_key, str) or len(blinding_key.encode("utf-8")) < 32:
        raise AnnotationPreparationError("blinding key must contain at least 32 UTF-8 bytes")
    key = blinding_key.encode("utf-8")
    analysis_contract = manifest.get("analysisContract")
    if not isinstance(analysis_contract, dict):
        raise AnnotationPreparationError("formal manifest is missing its analysis contract")
    manifest_hash = canonical_hash(manifest)
    analysis_contract_hash = canonical_hash(analysis_contract)
    variants = {item["taskId"]: item for item in suite["variants"]}
    public_samples = []
    private_records = []
    shared_catalog = None

    for run in records:
        variant = variants.get(run["taskId"])
        if variant is None:
            raise AnnotationPreparationError(f"unknown taskId in run: {run['taskId']}")
        goal_definitions = {item["id"]: item for item in variant["goals"]}
        episode = run.get("episode")
        if not isinstance(episode, dict) or episode.get("formalExperiment") is not True or episode.get("fixture") is not False:
            raise AnnotationPreparationError(f"run {run['runId']} has invalid episode provenance")
        catalog = episode.get("catalog")
        if not isinstance(catalog, dict):
            raise AnnotationPreparationError(f"run {run['runId']} is missing the formal catalog snapshot")
        if shared_catalog is None:
            shared_catalog = deepcopy(catalog)
        elif shared_catalog != catalog:
            raise AnnotationPreparationError("formal runs do not share the same registered catalog")
        for round_record in episode.get("rounds", []):
            if round_record.get("status") in {"trusted_setup", "history_setup"}:
                continue
            if round_record.get("phase") == "distractor":
                continue
            round_number = round_record.get("round")
            if not isinstance(round_number, int):
                raise AnnotationPreparationError("round number is missing")
            before = round_record.get("beforeDocument")
            after = round_record.get("afterDocument")
            if not isinstance(before, dict) or not isinstance(after, dict):
                raise AnnotationPreparationError("formal rounds require before/after state snapshots")
            base_identity = f"{manifest['experimentId']}:{run['runId']}:{round_number}"
            semantic_id = _blind_id(key, "sample", "semantic:" + base_identity)
            blinder = IdentityBlinder(key, semantic_id, [before, after])

            tested_goals = []
            turn_goal = round_record.get("turnGoal")
            if not isinstance(turn_goal, dict):
                raise AnnotationPreparationError("formal round is missing its immediate turn goal")
            tested_goals.append({
                "goalId": turn_goal["id"],
                "weight": turn_goal["weight"],
                "introducedRound": turn_goal["introducedRound"],
                "semanticRubric": turn_goal["semanticRubric"],
                "goalType": "immediate_instruction",
            })
            for goal_id in round_record.get("goalEvents", {}).get("test", []):
                goal = goal_definitions.get(goal_id)
                if goal is None:
                    raise AnnotationPreparationError(f"unknown tested goal: {goal_id}")
                tested_goals.append({
                    "goalId": goal["id"],
                    "weight": goal["weight"],
                    "introducedRound": goal["introducedRound"],
                    "semanticRubric": goal["semanticRubric"],
                    "goalType": goal["kind"],
                })
            context_goals = []
            for goal_id in round_record.get("activeGoalIds", []):
                goal = goal_definitions.get(goal_id)
                if goal is None:
                    raise AnnotationPreparationError(f"unknown active goal: {goal_id}")
                context_goals.append({
                    "goalId": goal["id"],
                    "weight": goal["weight"],
                    "semanticRubric": goal["semanticRubric"],
                    "goalType": goal["kind"],
                })
            retired_goals = []
            for goal_id in round_record.get("goalEvents", {}).get("retire", []):
                goal = goal_definitions.get(goal_id)
                if goal is None:
                    raise AnnotationPreparationError(f"unknown retired goal: {goal_id}")
                retired_goals.append({
                    "goalId": goal["id"],
                    "semanticRubric": goal["semanticRubric"],
                })

            status = round_record.get("status")
            requires_human = status in SUCCESSFUL_OUTPUT_STATUSES
            public_samples.append({
                "sampleId": semantic_id,
                "sampleType": "semantic_round",
                "taskId": run["taskId"],
                "round": round_number,
                "phase": round_record.get("phase"),
                "instruction": round_record.get("instruction"),
                "contextGoals": context_goals,
                "goalsToLabel": tested_goals,
                "retiredGoalsToLabel": retired_goals,
                "allowedPaths": [blinder.pointer(item) for item in round_record.get("allowedPaths", [])],
                "candidateTargetCount": round_record.get("candidateTargetCount"),
                "beforeDocument": blinder.document(before),
                "systemOutput": {
                    "status": status,
                    "decision": _system_decision(status),
                    "message": _parse_message(round_record.get("rawResponse")),
                    "afterDocument": blinder.document(after),
                },
                "requiresHumanJudgment": requires_human,
                "expectedLabels": {
                    "goalIds": [item["goalId"] for item in tested_goals],
                    "staleGoalIds": [item["goalId"] for item in retired_goals],
                    "candidateIds": [],
                    "conflictLabel": True,
                },
            })
            private_records.append({
                "sampleId": semantic_id,
                "sampleType": "semantic_round",
                "runId": run["runId"],
                "blockId": run["blockId"],
                "modelKey": run["modelKey"],
                "method": run["method"],
                "taskId": run["taskId"],
                "trial": run["trial"],
                "round": round_number,
                "expectedBehavior": round_record.get("expectedBehavior"),
                "groundTruthDecision": _ground_truth_decision(round_record.get("expectedBehavior")),
                "systemDecision": _system_decision(status),
                "technicalFailure": not requires_human,
                "candidateMap": [],
            })

            candidate_documents = _candidate_documents(before, round_record)
            if candidate_documents:
                candidate_sample_id = _blind_id(key, "sample", "candidates:" + base_identity)
                candidate_blinder = IdentityBlinder(
                    key,
                    candidate_sample_id,
                    [before, *[item["document"] for item in candidate_documents]],
                )
                public_candidates = []
                private_candidates = []
                chosen_strategy = round_record.get("application", {}).get("chosenStrategy")
                for candidate in candidate_documents:
                    candidate_id = _blind_id(key, "candidate", base_identity + ":" + candidate["strategy"])
                    public_candidates.append({
                        "candidateId": candidate_id,
                        "afterDocument": candidate_blinder.document(candidate["document"]),
                    })
                    private_candidates.append({
                        "candidateId": candidate_id,
                        "strategy": candidate["strategy"],
                        "systemRank": candidate["systemRank"],
                        "selectedTop1": candidate["strategy"] == chosen_strategy,
                    })
                public_candidates.sort(key=lambda item: _blind_digest(key, "candidate-order", item["candidateId"], 64))
                public_samples.append({
                    "sampleId": candidate_sample_id,
                    "sampleType": "candidate_batch",
                    "taskId": run["taskId"],
                    "round": round_number,
                    "phase": round_record.get("phase"),
                    "instruction": round_record.get("instruction"),
                    "contextGoals": [*context_goals, *tested_goals[:1]],
                    "candidateTargetCount": round_record.get("candidateTargetCount"),
                    "beforeDocument": candidate_blinder.document(before),
                    "candidates": public_candidates,
                    "requiresHumanJudgment": True,
                    "expectedLabels": {
                        "goalIds": [],
                        "staleGoalIds": [],
                        "candidateIds": [item["candidateId"] for item in public_candidates],
                        "conflictLabel": False,
                    },
                })
                private_records.append({
                    "sampleId": candidate_sample_id,
                    "sampleType": "candidate_batch",
                    "runId": run["runId"],
                    "blockId": run["blockId"],
                    "modelKey": run["modelKey"],
                    "method": run["method"],
                    "taskId": run["taskId"],
                    "trial": run["trial"],
                    "round": round_number,
                    "expectedBehavior": round_record.get("expectedBehavior"),
                    "groundTruthDecision": "not_applicable",
                    "systemDecision": "not_applicable",
                    "technicalFailure": False,
                    "candidateMap": private_candidates,
                })

    order = lambda item: _blind_digest(key, "sample-order", item["sampleId"], 64)
    public_samples.sort(key=order)
    private_records.sort(key=order)
    packet = {
        "schemaVersion": PACKET_SCHEMA_VERSION,
        "generatedAt": _now(),
        "formalExperiment": True,
        "blinded": True,
        "experimentId": manifest["experimentId"],
        "manifestHash": manifest_hash,
        "analysisContractHash": analysis_contract_hash,
        "benchmarkHash": suite["compiledHash"],
        "benchmarkSourceHash": suite["sourceHash"],
        "promptHash": manifest["configuration"]["promptHash"],
        "catalog": shared_catalog,
        "notes": [
            "Method, model, run identity, action envelope, strategy, rule score and system rank are withheld.",
            "Identity fields are per-sample HMAC pseudonyms; place/catalog identifiers remain visible for semantic judging.",
            "Samples with requiresHumanJudgment=false are retained for completeness but scored as preregistered technical failures.",
            "Human semantic/candidate units cover combined, probe, and model-executed setup rounds; distractor rounds use explicitly marked structural proxies in scoring.",
        ],
        "sampleCount": len(public_samples),
        "samples": public_samples,
    }
    packet_schema = json.loads(PACKET_SCHEMA.read_text(encoding="utf-8"))
    packet_errors = sorted(Draft202012Validator(packet_schema).iter_errors(packet), key=lambda item: list(item.absolute_path))
    if packet_errors:
        rendered = [f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in packet_errors[:20]]
        raise AnnotationPreparationError("generated packet schema validation failed:\n" + "\n".join(rendered))
    mapping_payload = {
        "schemaVersion": PACKET_SCHEMA_VERSION,
        "generatedAt": packet["generatedAt"],
        "private": True,
        "experimentId": manifest["experimentId"],
        "manifestHash": manifest_hash,
        "analysisContractHash": analysis_contract_hash,
        "benchmarkHash": suite["compiledHash"],
        "benchmarkSourceHash": suite["sourceHash"],
        "promptHash": manifest["configuration"]["promptHash"],
        "packetHash": canonical_hash(packet),
        "blindingKeyFingerprint": hashlib.sha256(key).hexdigest(),
        "sampleCount": len(private_records),
        "records": private_records,
    }
    mapping = deepcopy(mapping_payload)
    mapping["recordHash"] = canonical_hash(mapping_payload)
    return packet, mapping


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--private-map", type=Path, required=True)
    parser.add_argument("--blinding-key-env", default="ANNOTATION_BLINDING_KEY")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPTS)
    args = parser.parse_args()
    if args.packet.resolve() == args.private_map.resolve():
        parser.error("--packet and --private-map must be different files")
    if args.packet.exists() or args.private_map.exists():
        parser.error("annotation outputs must not already exist")
    blinding_key = os.environ.get(args.blinding_key_env, "")
    try:
        manifest, suite, records = load_completed_formal_results(
            args.results,
            tasks_path=args.tasks,
            schema_path=args.schema,
            prompt_directory=args.prompt_dir,
        )
        packet, mapping = build_blinded_exports(manifest, suite, records, blinding_key)
        _atomic_create_json(args.packet.resolve(), packet)
        _atomic_create_json(args.private_map.resolve(), mapping)
    except (AnnotationPreparationError, RunnerStateError, OSError, ValueError) as error:
        print(json.dumps({"created": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({
        "created": True,
        "sampleCount": packet["sampleCount"],
        "packet": str(args.packet),
        "privateMap": str(args.private_map),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
