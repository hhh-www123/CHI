#!/usr/bin/env python3
"""Merge formal run state with resolved blinded labels into score.mjs input.

This step refuses incomplete annotation sets by default. It computes structural
scope and state-constraint fields deterministically; human labels are used only
for semantic goals, stale-goal leakage, and candidate relevance.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

try:
    from .annotation_resolution import AnnotationResolutionError, resolve_annotations
    from .prepare_blinded_annotations import load_completed_formal_results
    from .run_real_experiment import RunnerStateError, _atomic_create_json
    from .task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash
    from .prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS
except ImportError:
    from annotation_resolution import AnnotationResolutionError, resolve_annotations
    from prepare_blinded_annotations import load_completed_formal_results
    from run_real_experiment import RunnerStateError, _atomic_create_json
    from task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash
    from prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS


ROOT = REPOSITORY_ROOT
DEFAULT_ANNOTATION_SCHEMA = ROOT / "evaluation" / "annotations" / "schema.json"
CONSTRAINT_GOAL_KINDS = {"hard_constraint", "self_coherence", "goal_update", "conflict_handling"}
FAILURE_STATUSES = {
    "model_error", "invalid_output", "state_rejected", "guard_rejected",
    "version_conflict", "restore_target_missing", "post_restore_guard_rejected",
    "no_change",
}


class ScoringAssemblyError(ValueError):
    pass


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScoringAssemblyError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise ScoringAssemblyError(f"JSON must contain an object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ScoringAssemblyError(f"cannot hash scoring asset: {path}") from error


def _pointer(value, path: str):
    if path == "":
        return value
    if not path.startswith("/"):
        raise ScoringAssemblyError(f"invalid assertion pointer: {path}")
    current = value
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise ScoringAssemblyError(f"assertion pointer does not exist: {path}")
        current = current[token]
    return current


def _day_snapshot(document: dict, day_number: int):
    order = document.get("dayOrder", [])
    if not isinstance(order, list) or not 1 <= day_number <= len(order):
        raise ScoringAssemblyError(f"document has no day{day_number}")
    day_id = order[day_number - 1]
    day = deepcopy(document["entities"]["days"][day_id])
    activities = {
        activity_id: deepcopy(document["entities"]["activities"][activity_id])
        for activity_id in day["activityIds"]
    }
    components = {
        key: deepcopy(item)
        for key, item in document["ui"]["components"].items()
        if item.get("entityId") == day_id or item.get("entityId") in activities
    }
    return {"day": day, "activities": activities, "components": components}


def _resolve_assertion_value(
    path: str,
    *,
    round_record: dict,
    baseline_document: dict,
    baseline_session_facts: dict,
    catalog: dict,
    use_baseline: bool = False,
):
    document = baseline_document if use_baseline else round_record["afterDocument"]
    session_facts = baseline_session_facts if use_baseline else round_record["sessionFacts"]
    guard_state = round_record["guardState"]
    if path.startswith("/document"):
        relative = path.removeprefix("/document")
        if "{probeActivity}" in relative or "{day2Stop2}" in relative:
            allowed = [item for item in round_record.get("allowedPaths", []) if item.startswith("/entities/activities/")]
            if not allowed:
                raise ScoringAssemblyError(f"cannot resolve activity placeholder in {path}")
            activity_id = allowed[0].split("/")[3]
            relative = relative.replace("{probeActivity}", activity_id).replace("{day2Stop2}", activity_id)
        return _pointer(document, relative)
    if path == "/session/tripState/hotelId":
        return session_facts["hotelId"]
    day_match = re.fullmatch(r"/session/tripState/days/day(\d+)", path)
    if day_match:
        return _day_snapshot(document, int(day_match.group(1)))
    if path == "/session/controlState/hotelLocked":
        return round_record["guardState"]["controlState"].get("hotelLocked") is True
    day_lock = re.fullmatch(r"/session/controlState/day(\d+)Locked", path)
    if day_lock:
        return f"day-{day_lock.group(1)}" in round_record["guardState"]["controlState"].get("lockedRegions", [])
    if path == "/session/persistedConstraints/max_budget":
        values = [
            item.get("value")
            for item in guard_state["persistedConstraints"].get("hardConstraints", [])
            if isinstance(item, dict) and item.get("kind") == "max_budget" and item.get("active") is True
        ]
        return values
    if path == "/audit/restoreFromVersion":
        return round_record.get("application", {}).get("restoreFromVersion")
    raise ScoringAssemblyError(f"unsupported registered assertion path: {path}")


def _assertion_satisfied(
    assertion: dict,
    *,
    round_record: dict,
    baseline_document: dict,
    baseline_session_facts: dict,
    catalog: dict,
) -> bool:
    path = assertion["path"]
    operator = assertion["operator"]
    current = _resolve_assertion_value(
        path,
        round_record=round_record,
        baseline_document=baseline_document,
        baseline_session_facts=baseline_session_facts,
        catalog=catalog,
    )
    if operator == "equals":
        return current == assertion.get("value")
    if operator == "not_equals":
        value = assertion.get("value")
        if value == "{restoreSnapshotValue}":
            raise ScoringAssemblyError("restoreSnapshotValue requires semantic/version adjudication")
        return current != value
    if operator == "less_or_equal":
        return isinstance(current, (int, float)) and not isinstance(current, bool) and current <= assertion["value"]
    if operator == "unchanged":
        baseline = _resolve_assertion_value(
            path,
            round_record=round_record,
            baseline_document=baseline_document,
            baseline_session_facts=baseline_session_facts,
            catalog=catalog,
            use_baseline=True,
        )
        return current == baseline
    if operator in {"active", "inactive"}:
        active = assertion.get("value") in current
        return active if operator == "active" else not active
    if operator == "catalog_property":
        places = {item["id"]: item for item in catalog.get("places", [])}
        return current in places and places[current].get(assertion["property"]) == assertion.get("value")
    raise ScoringAssemblyError(f"unsupported assertion operator: {operator}")


def _scope_fields(round_record: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    expected = round_record["expectedBehavior"]
    status = round_record["status"]
    allowed = list(dict.fromkeys(round_record.get("allowedPaths", [])))
    derived_authorized = []
    for path in allowed:
        if path.endswith("/placeId"):
            prefix = path.rsplit("/", 1)[0]
            derived_authorized.extend([prefix + "/title", prefix + "/description"])
    raw_actual = list(dict.fromkeys(round_record.get("changedPaths", [])))
    derived_changes = []
    if expected == "edit":
        desired = allowed
        authorized = set([*allowed, *derived_authorized])
        derived_changes = [item for item in raw_actual if item in derived_authorized]
        actual = [item for item in raw_actual if item not in derived_authorized]
    elif expected == "restore":
        actual = raw_actual
        if status == "restored":
            # Version-target correctness is scored semantically. Once the
            # explicit restore tool is accepted, its replay diff is authorized.
            desired = actual
            authorized = set(actual)
        else:
            desired = allowed
            authorized = set(allowed)
    else:
        actual = raw_actual
        desired = []
        authorized = set()
    unauthorized = [item for item in actual if item not in authorized]
    return desired, actual, unauthorized, derived_changes


def _registered_decisions(expected: str, status: str) -> tuple[str, str]:
    ground_truth = "clarify" if expected == "clarify" else "reject" if expected == "reject" else "accept"
    system = "clarify" if status == "clarify" else "reject" if status == "reject" else "accept" if status in {"applied", "restored", "constraint_updated"} else "not_applicable"
    return ground_truth, system


def _round_call_event(run: dict, round_number: int) -> dict | None:
    matches = [item for item in run.get("callEvents", []) if item.get("round") == round_number]
    if len(matches) > 1:
        raise ScoringAssemblyError(f"run {run['runId']} has duplicate call events for round {round_number}")
    return matches[0] if matches else None


def _id_survival(before: dict, after: dict, *path: str) -> float | None:
    left = before
    right = after
    for key in path:
        left = left.get(key, {}) if isinstance(left, dict) else {}
        right = right.get(key, {}) if isinstance(right, dict) else {}
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ScoringAssemblyError("identity survival paths must resolve to objects")
    return len(set(left) & set(right)) / len(left) if left else None


def _semantic_mapping(private_records: dict[str, dict], run_id: str, round_number: int, sample_type: str) -> dict | None:
    matches = [
        item for item in private_records.values()
        if item["runId"] == run_id and item["round"] == round_number and item["sampleType"] == sample_type
    ]
    if len(matches) > 1:
        raise ScoringAssemblyError(f"duplicate {sample_type} mapping for {run_id}/round {round_number}")
    return matches[0] if matches else None


def _execution_position(manifest: dict, run: dict) -> int:
    """Recover and verify the randomized within-block execution position."""
    blocks = [item for item in manifest.get("blocks", []) if item.get("blockId") == run.get("blockId")]
    if len(blocks) != 1:
        raise ScoringAssemblyError(f"run {run.get('runId')} has no unique manifest block")
    order = blocks[0].get("methodOrder")
    if not isinstance(order, list) or order.count(run.get("method")) != 1:
        raise ScoringAssemblyError(f"run {run.get('runId')} has an invalid manifest method order")
    expected = order.index(run["method"]) + 1
    recorded = run.get("orderIndex")
    if recorded is not None and (not isinstance(recorded, int) or isinstance(recorded, bool) or recorded + 1 != expected):
        raise ScoringAssemblyError(f"run {run.get('runId')} execution position does not match the manifest")
    return expected


def build_scoring_dataset(
    manifest: dict,
    suite: dict,
    runs: list[dict],
    packet: dict,
    mapping: dict,
    resolved: dict[str, dict],
    annotation_stats: dict,
    *,
    require_formal_annotations: bool = True,
) -> dict:
    if require_formal_annotations and annotation_stats.get("formalReady") is not True:
        raise ScoringAssemblyError("annotation set is not formal-ready: " + "; ".join(annotation_stats.get("blockers", [])))
    analysis_contract = manifest.get("analysisContract")
    if not isinstance(analysis_contract, dict):
        raise ScoringAssemblyError("formal manifest is missing its analysis contract")
    if analysis_contract.get("scoringAssemblyHash") != _file_sha256(Path(__file__).resolve()):
        raise ScoringAssemblyError("running scoring assembler does not match the formal analysis contract")
    if analysis_contract.get("annotationResolutionHash") != _file_sha256(ROOT / "evaluation" / "annotation_resolution.py"):
        raise ScoringAssemblyError("running annotation resolver does not match the formal analysis contract")
    if analysis_contract.get("annotationSchemaHash") != _file_sha256(DEFAULT_ANNOTATION_SCHEMA):
        raise ScoringAssemblyError("annotation schema does not match the formal analysis contract")
    if analysis_contract.get("annotationRubricHash") != _file_sha256(ROOT / "research" / "annotation-rubric.md"):
        raise ScoringAssemblyError("annotation rubric does not match the formal analysis contract")
    expected_provenance = {
        "experimentId": manifest.get("experimentId"),
        "manifestHash": canonical_hash(manifest),
        "analysisContractHash": canonical_hash(analysis_contract),
        "benchmarkHash": suite.get("compiledHash"),
        "benchmarkSourceHash": suite.get("sourceHash"),
        "promptHash": manifest.get("configuration", {}).get("promptHash"),
    }
    for artifact_name, artifact in (("packet", packet), ("private mapping", mapping)):
        for key, value in expected_provenance.items():
            if artifact.get(key) != value:
                raise ScoringAssemblyError(f"{artifact_name} {key} does not match the formal manifest")
    public_samples = {item["sampleId"]: item for item in packet["samples"]}
    private_records = {item["sampleId"]: item for item in mapping["records"]}
    variants = {item["taskId"]: item for item in suite["variants"]}
    episodes = []

    for run in runs:
        variant = variants[run["taskId"]]
        goals = {item["id"]: item for item in variant["goals"]}
        episode_record = run["episode"]
        rounds = episode_record["rounds"]
        first_state_round = next((item for item in rounds if isinstance(item.get("afterDocument"), dict)), None)
        if first_state_round is None:
            raise ScoringAssemblyError(f"run {run['runId']} has no state snapshots")
        baseline_document = next(
            (deepcopy(item["beforeDocument"]) for item in rounds if isinstance(item.get("beforeDocument"), dict)),
            deepcopy(first_state_round["afterDocument"]),
        )
        baseline_session_facts = deepcopy(first_state_round["sessionFacts"])
        catalog = episode_record["catalog"]
        scored_rounds = []

        for round_record in rounds:
            round_number = round_record["round"]
            desired, actual, unauthorized, derived_changes = _scope_fields(round_record)
            trusted_setup = round_record["status"] in {"trusted_setup", "history_setup"}
            semantic_map = _semantic_mapping(private_records, run["runId"], round_number, "semantic_round")
            if trusted_setup:
                turn_satisfied = True
                semantic_labels = None
                system_matches_expected = True
                ground_truth_decision = "not_applicable"
                system_decision = "not_applicable"
            else:
                if semantic_map is None:
                    if round_record["phase"] != "distractor":
                        raise ScoringAssemblyError(f"missing semantic sample mapping for registered human round {run['runId']}/round {round_number}")
                    ground_truth_decision, system_decision = _registered_decisions(round_record["expectedBehavior"], round_record["status"])
                    system_matches_expected = ground_truth_decision == system_decision
                    semantic_labels = None
                    turn_satisfied = (
                        system_matches_expected
                        and round_record["status"] not in FAILURE_STATUSES
                        and not unauthorized
                        and (round_record["expectedBehavior"] != "edit" or bool(actual))
                    )
                    turn_assessment = "structural_proxy"
                else:
                    system_matches_expected = semantic_map["groundTruthDecision"] == semantic_map["systemDecision"]
                    ground_truth_decision = semantic_map["groundTruthDecision"]
                    system_decision = semantic_map["systemDecision"]
                    if semantic_map["technicalFailure"]:
                        turn_satisfied = False
                        semantic_labels = None
                    else:
                        semantic_labels = resolved.get(semantic_map["sampleId"])
                        if semantic_labels is None:
                            raise ScoringAssemblyError(f"missing resolved labels for {semantic_map['sampleId']}")
                        turn_id = round_record["turnGoal"]["id"]
                        turn_satisfied = semantic_labels["goalScores"].get(turn_id) == 2 and system_matches_expected
                    turn_assessment = "human_blinded"

            active_goals = []
            for goal_id in round_record["goalEvents"]["test"]:
                goal = goals[goal_id]
                score = 0 if semantic_labels is None else semantic_labels["goalScores"].get(goal_id, 0)
                active_goals.append({
                    "id": goal_id,
                    "introducedRound": goal["introducedRound"],
                    "weight": goal["weight"],
                    "satisfied": score == 2,
                    "ordinalScore": score,
                    "retentionEligible": goal["retentionEligible"],
                })
            retired_goals = []
            for goal_id in round_record["goalEvents"]["retire"]:
                leaked = True if semantic_labels is None else semantic_labels["staleGoalLeaks"].get(goal_id, True)
                retired_goals.append({"id": goal_id, "leaked": leaked})

            active_constraints = []
            for goal_id in round_record.get("activeGoalIds", []):
                goal = goals[goal_id]
                assertions = goal.get("stateAssertions", [])
                if goal["kind"] not in CONSTRAINT_GOAL_KINDS or not assertions:
                    continue
                satisfied = all(_assertion_satisfied(
                    assertion,
                    round_record=round_record,
                    baseline_document=baseline_document,
                    baseline_session_facts=baseline_session_facts,
                    catalog=catalog,
                ) for assertion in assertions)
                active_constraints.append({
                    "id": goal_id,
                    "introducedRound": goal["introducedRound"],
                    "weight": goal["weight"],
                    "satisfied": satisfied,
                })

            candidates = None
            application = round_record.get("application", {})
            if "acceptedCandidates" in application or "rejectedCandidates" in application:
                candidates = []
                candidate_map = _semantic_mapping(private_records, run["runId"], round_number, "candidate_batch")
                candidate_labels = None
                candidate_sample = None
                if candidate_map is not None:
                    candidate_sample = public_samples[candidate_map["sampleId"]]
                    candidate_labels = resolved.get(candidate_map["sampleId"])
                    if candidate_labels is None:
                        raise ScoringAssemblyError(f"missing candidate labels for {candidate_map['sampleId']}")
                    public_candidates = {item["candidateId"]: item for item in candidate_sample["candidates"]}
                    for item in candidate_map["candidateMap"]:
                        label = candidate_labels["candidateLabels"][item["candidateId"]]
                        candidates.append({
                            "valid": True,
                            "outcomeSignature": canonical_hash(public_candidates[item["candidateId"]]["afterDocument"]),
                            "systemRank": item["systemRank"],
                            "humanRelevance": label["relevance"],
                            "humanBest": label["humanBest"],
                        })
                else:
                    for rank, item in enumerate(application.get("acceptedCandidates", []), 1):
                        candidates.append({
                            "valid": True,
                            "outcomeSignature": canonical_hash(item["patch"]),
                            "systemRank": rank,
                        })
                for _ in application.get("rejectedCandidates", []):
                    candidates.append({"valid": False})

            call_event = _round_call_event(run, round_number)
            model_metadata = round_record.get("modelMetadata") or (call_event or {}).get("modelMetadata") or {}
            usage = (call_event or {}).get("usage") or {}
            semantic_failure = (
                not turn_satisfied
                or not system_matches_expected
                or any(not item["satisfied"] for item in active_goals)
                or any(item["leaked"] for item in retired_goals)
                or round_record["status"] in FAILURE_STATUSES
            )
            scored = {
                "round": round_number,
                "phase": round_record["phase"],
                "turnGoal": {
                    "id": round_record["turnGoal"]["id"],
                    "satisfied": turn_satisfied,
                    "assessment": "trusted_setup" if trusted_setup else turn_assessment,
                },
                "activeGoals": active_goals,
                "retiredGoals": retired_goals,
                "activeConstraints": active_constraints,
                "desiredPaths": desired,
                "actualChangedPaths": actual,
                "unauthorizedChangedPaths": unauthorized,
                "derivedChangedPaths": derived_changes,
                "scopeApplicable": not trusted_setup,
                "semanticFailure": semantic_failure,
                "systemDecisionMatchesGroundTruth": system_matches_expected,
                "groundTruthDecision": ground_truth_decision,
                "systemDecision": system_decision,
                "status": round_record["status"],
                "candidateTargetCount": round_record.get("candidateTargetCount"),
                "latencyMs": model_metadata.get("latencyMs"),
                "inputTokens": usage.get("inputTokens", model_metadata.get("inputTokens")),
                "outputTokens": usage.get("outputTokens", model_metadata.get("outputTokens")),
                "apiCostUsd": (call_event or {}).get("observedCostUsd"),
                "budgetDebitUsd": (call_event or {}).get("budgetDebitUsd"),
                "componentIdSurvivalRate": _id_survival(round_record["beforeDocument"], round_record["afterDocument"], "ui", "components") if not trusted_setup else 1,
                "activityIdSurvivalRate": _id_survival(round_record["beforeDocument"], round_record["afterDocument"], "entities", "activities") if not trusted_setup else 1,
            }
            if candidates is not None:
                scored["candidates"] = candidates
            identity_attempts = application.get("systemIdentityOverrides", [])
            if identity_attempts:
                scored["attemptedSystemIdentityChanges"] = deepcopy(identity_attempts)
            scored_rounds.append(scored)

        success = all(
            item["turnGoal"]["satisfied"]
            and not item["semanticFailure"]
            and not item["unauthorizedChangedPaths"]
            and all(goal["satisfied"] for goal in item["activeGoals"])
            and all(constraint["satisfied"] for constraint in item["activeConstraints"])
            and all(not goal["leaked"] for goal in item["retiredGoals"])
            for item in scored_rounds
        )
        episode_output = {
            "runId": run["runId"],
            "blockId": run["blockId"],
            "executionPosition": _execution_position(manifest, run),
            "method": run["method"],
            "modelKey": run["modelKey"],
            "taskId": run["taskId"],
            "templateId": variant["templateId"],
            "family": run["family"],
            "interactionLength": run["interactionLength"],
            "trial": run["trial"],
            "success": success,
            "rounds": scored_rounds,
        }
        if run["family"] == "versioned_editing":
            probe = scored_rounds[-1]
            restore_success = (
                probe["status"] == "restored"
                and probe["turnGoal"]["satisfied"]
                and all(item["satisfied"] for item in probe["activeGoals"])
                and not probe["unauthorizedChangedPaths"]
            )
            episode_output["versionRecovery"] = {
                "success": restore_success,
                "residualErrors": 0 if restore_success else 1,
                "targetRound": probe["round"],
            }
        if run["family"] == "goal_update":
            probe = scored_rounds[-1]
            episode_output["goalUpdate"] = {
                "success": (
                    probe["status"] == "constraint_updated"
                    and probe["turnGoal"]["satisfied"]
                    and all(item["satisfied"] for item in probe["activeGoals"])
                    and all(not item["leaked"] for item in probe["retiredGoals"])
                ),
            }
        episodes.append(episode_output)

    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "illustrative": False,
        "pilot": False,
        "formalExperiment": True,
        "generationMode": "real-model-formal-runner",
        "realModel": True,
        "realMap": manifest["configuration"].get("mapMode") == "real",
        "experimentId": manifest["experimentId"],
        "manifestHash": canonical_hash(manifest),
        "analysisContract": deepcopy(analysis_contract),
        "analysisContractHash": canonical_hash(analysis_contract),
        "benchmarkHash": suite["compiledHash"],
        "benchmarkSourceHash": suite["sourceHash"],
        "promptHash": manifest["configuration"]["promptHash"],
        "annotationStats": deepcopy(annotation_stats),
        "notes": [
            "Structural scope and registered state constraints are deterministic.",
            "Semantic, stale-goal and candidate relevance fields come from resolved blinded human labels.",
            "Technical/model contract failures are retained and scored as failures, never imputed.",
        ],
        "episodes": episodes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--private-map", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotation-schema", type=Path, default=DEFAULT_ANNOTATION_SCHEMA)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPTS)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must not already exist")
    try:
        manifest, suite, runs = load_completed_formal_results(
            args.results,
            tasks_path=args.tasks,
            schema_path=args.schema,
            prompt_directory=args.prompt_dir,
        )
        if manifest.get("analysisContract", {}).get("annotationSchemaHash") != _file_sha256(args.annotation_schema.resolve()):
            raise ScoringAssemblyError("selected annotation schema does not match the formal analysis contract")
        packet = _read(args.packet)
        mapping = _read(args.private_map)
        annotations = _read(args.annotations)
        schema = _read(args.annotation_schema)
        resolved, stats = resolve_annotations(packet, mapping, annotations, schema)
        dataset = build_scoring_dataset(
            manifest, suite, runs, packet, mapping, resolved, stats,
            require_formal_annotations=True,
        )
        _atomic_create_json(args.output.resolve(), dataset)
    except (ScoringAssemblyError, AnnotationResolutionError, RunnerStateError, OSError, ValueError) as error:
        print(json.dumps({"created": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({
        "created": True,
        "episodeCount": len(dataset["episodes"]),
        "formalExperiment": True,
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
