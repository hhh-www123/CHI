#!/usr/bin/env python3
"""Validate and deterministically compile long-horizon task templates.

This module never calls a model. A compiled suite is an experiment input, not a
result, and retains the source status so draft tasks cannot be mistaken for a
frozen benchmark.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "evaluation" / "tasks" / "benchmark-v1.json"
DEFAULT_SCHEMA = ROOT / "evaluation" / "tasks" / "schema.json"
EXPECTED_FAMILIES = {
    "instruction_retention",
    "implicit_preference",
    "versioned_editing",
    "self_coherence",
    "goal_update",
    "conflict_handling",
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return sorted(duplicate)


def validate_suite(suite: dict, schema: dict) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(suite), key=lambda item: list(item.absolute_path))
    if errors:
        rendered = []
        for error in errors[:20]:
            location = "/" + "/".join(str(item) for item in error.absolute_path)
            rendered.append(f"{location}: {error.message}")
        raise ValueError("task schema validation failed:\n" + "\n".join(rendered))

    problems: list[str] = []
    if suite["lengths"] != [1, 5, 10, 20]:
        problems.append("lengths must be fixed in ascending order as [1, 5, 10, 20]")

    distractor_ids = [item["id"] for item in suite["distractorPool"]]
    duplicate_distractors = _duplicates(distractor_ids)
    if duplicate_distractors:
        problems.append(f"duplicate distractor ids: {duplicate_distractors}")
    distractor_set = set(distractor_ids)

    task_ids = [item["id"] for item in suite["templates"]]
    duplicate_tasks = _duplicates(task_ids)
    if duplicate_tasks:
        problems.append(f"duplicate task ids: {duplicate_tasks}")
    families = [item["family"] for item in suite["templates"]]
    if set(families) != EXPECTED_FAMILIES or len(families) != len(EXPECTED_FAMILIES):
        problems.append("the core suite requires exactly one template from each of the six registered families")

    needed_distractors = max(suite["lengths"]) - 2
    for distractor in suite["distractorPool"]:
        target = distractor["target"]
        if target["kind"] == "activity" and not {"dayKey", "stopId"} <= target.keys():
            problems.append(f"{distractor['id']} activity target requires dayKey and stopId")
        if (distractor["expectedBehavior"] == "edit") != (distractor["candidateTargetCount"] is not None):
            problems.append(f"{distractor['id']} candidate target must be set exactly for edit behavior")
    for template in suite["templates"]:
        unknown = sorted(set(template["distractorIds"]) - distractor_set)
        if unknown:
            problems.append(f"{template['id']} references unknown distractors: {unknown}")
        if len(template["distractorIds"]) < needed_distractors:
            problems.append(f"{template['id']} needs at least {needed_distractors} distractors")
        duplicate_order = _duplicates(template["distractorIds"])
        if duplicate_order:
            problems.append(f"{template['id']} repeats distractors: {duplicate_order}")
        goal_ids = [item["id"] for item in template["goalLedger"]]
        duplicate_goals = _duplicates(goal_ids)
        if duplicate_goals:
            problems.append(f"{template['id']} has duplicate goal ids: {duplicate_goals}")
        for phase in ("setup", "probe"):
            target = template[phase + "Target"]
            fields = template[phase + "Fields"]
            if target["kind"] == "activity" and not {"dayKey", "stopId"} <= target.keys():
                problems.append(f"{template['id']} {phase} activity target requires dayKey and stopId")
            if any(field != "none" for field in fields) and target["kind"] == "version" and not {"dayKey", "stopId"} <= target.keys():
                problems.append(f"{template['id']} {phase} version action with editable fields requires the incremental activity target")
            behavior = template[phase + "ExpectedBehavior"] if phase == "setup" else template["oracle"]["probeExpectedBehavior"]
            candidate_target = template[phase + "CandidateTargetCount"]
            model_called = phase == "probe" or (not template["trustedSetupActions"] and target["kind"] != "conversation")
            if (model_called and behavior in {"edit", "restore"}) != (candidate_target is not None):
                problems.append(f"{template['id']} {phase} candidate target must be set exactly for edit/restore behavior")
            if not model_called and candidate_target is not None:
                problems.append(f"{template['id']} {phase} candidate target must be null when trusted setup bypasses the model")
        if template["family"] == "goal_update":
            if not any(item["activeThrough"] == "replaced_at_probe" for item in template["goalLedger"]):
                problems.append(f"{template['id']} must explicitly retire an old goal")
            if not any(item["introducedBy"] == "probe" for item in template["goalLedger"]):
                problems.append(f"{template['id']} must introduce the replacement goal at the probe")

    if problems:
        raise ValueError("task semantic validation failed:\n" + "\n".join(f"- {item}" for item in problems))


def _combined_fields(template: dict) -> list[str]:
    values = list(dict.fromkeys([*template["setupFields"], *template["probeFields"]]))
    if len(values) > 1 and "none" in values:
        values.remove("none")
    return values


def _compiled_goals(template: dict, length: int) -> list[dict]:
    result = []
    for goal in template["goalLedger"]:
        item = deepcopy(goal)
        item["introducedRound"] = length if goal["introducedBy"] == "probe" and length > 1 else 1
        item["testedRound"] = length
        item["retiredRound"] = length if goal["activeThrough"] == "replaced_at_probe" else None
        item["retentionEligible"] = template["family"] != "goal_update"
        result.append(item)
    return result


def _active_after_probe(goals: list[dict]) -> list[str]:
    return [item["id"] for item in goals if item["activeThrough"] != "replaced_at_probe"]


def _turn_goal(round_number: int, instruction: str, expected_behavior: str) -> dict:
    """Register the immediate instruction separately from longitudinal goals."""
    return {
        "id": f"turn-r{round_number}",
        "introducedRound": round_number,
        "testedRound": round_number,
        "weight": 1,
        "semanticRubric": instruction,
        "expectedBehavior": expected_behavior,
    }


def compile_suite(suite: dict, schema: dict) -> dict:
    validate_suite(suite, schema)
    pool = {item["id"]: item for item in suite["distractorPool"]}
    variants: list[dict] = []

    for template in suite["templates"]:
        for length in suite["lengths"]:
            goals = _compiled_goals(template, length)
            introduced_setup = [item["id"] for item in goals if item["introducedBy"] in {"setup", "combined"}]
            introduced_probe = [item["id"] for item in goals if item["introducedBy"] == "probe"]
            retired_probe = [item["id"] for item in goals if item["activeThrough"] == "replaced_at_probe"]
            active_after_probe = _active_after_probe(goals)

            if length == 1:
                rounds = [{
                    "round": 1,
                    "phase": "combined",
                    "instruction": template["singleTurnInstruction"],
                    "target": deepcopy(template["probeTarget"]),
                    "fields": _combined_fields(template),
                    "expectedBehavior": template["oracle"]["probeExpectedBehavior"],
                    "candidateTargetCount": template["probeCandidateTargetCount"],
                    "goalEvents": {
                        "introduce": [item["id"] for item in goals],
                        "retire": retired_probe,
                        "test": active_after_probe,
                    },
                    "activeGoalIds": active_after_probe,
                    "turnGoal": _turn_goal(1, template["singleTurnInstruction"], template["oracle"]["probeExpectedBehavior"]),
                }]
            else:
                rounds = [{
                    "round": 1,
                    "phase": "setup",
                    "instruction": template["setupInstruction"],
                    "target": deepcopy(template["setupTarget"]),
                    "fields": deepcopy(template["setupFields"]),
                    "expectedBehavior": template["setupExpectedBehavior"],
                    "candidateTargetCount": template["setupCandidateTargetCount"],
                    "goalEvents": {"introduce": introduced_setup, "retire": [], "test": []},
                    "activeGoalIds": introduced_setup,
                    "turnGoal": _turn_goal(1, template["setupInstruction"], template["setupExpectedBehavior"]),
                }]
                for index, distractor_id in enumerate(template["distractorIds"][:length - 2], 2):
                    distractor = deepcopy(pool[distractor_id])
                    rounds.append({
                        "round": index,
                        "phase": "distractor",
                        "instruction": distractor["instruction"],
                        "target": distractor["target"],
                        "fields": distractor["fields"],
                        "expectedBehavior": distractor["expectedBehavior"],
                        "candidateTargetCount": distractor["candidateTargetCount"],
                        "sourceDistractorId": distractor_id,
                        "goalEvents": {"introduce": [], "retire": [], "test": []},
                        "activeGoalIds": introduced_setup,
                        "turnGoal": _turn_goal(index, distractor["instruction"], distractor["expectedBehavior"]),
                    })
                rounds.append({
                    "round": length,
                    "phase": "probe",
                    "instruction": template["probeInstruction"],
                    "target": deepcopy(template["probeTarget"]),
                    "fields": deepcopy(template["probeFields"]),
                    "expectedBehavior": template["oracle"]["probeExpectedBehavior"],
                    "candidateTargetCount": template["probeCandidateTargetCount"],
                    "goalEvents": {"introduce": introduced_probe, "retire": retired_probe, "test": active_after_probe},
                    "activeGoalIds": active_after_probe,
                    "turnGoal": _turn_goal(length, template["probeInstruction"], template["oracle"]["probeExpectedBehavior"]),
                })
                if len(rounds) != length:
                    raise AssertionError("compiled round count is inconsistent")

            variants.append({
                "taskId": f"{template['id']}-L{length}",
                "templateId": template["id"],
                "family": template["family"],
                "title": template["title"],
                "interactionLength": length,
                "goalDistance": max(0, length - 1),
                "initialFixture": suite["initialFixture"],
                "trustedSetupActions": deepcopy(template["trustedSetupActions"]),
                "goals": goals,
                "oracle": deepcopy(template["oracle"]),
                "rounds": rounds,
            })

    compiled_hash = canonical_hash({
        "benchmarkId": suite["benchmarkId"],
        "domain": suite["domain"],
        "variants": variants,
    })
    return {
        "schemaVersion": "1.0",
        "benchmarkId": suite["benchmarkId"],
        "sourceStatus": suite["status"],
        "sourceHash": canonical_hash(suite),
        "compiledHash": compiled_hash,
        "domain": suite["domain"],
        "variantCount": len(variants),
        "variants": variants,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check and args.output is None:
        parser.error("provide --check or --output")

    suite = read_json(args.source)
    schema = read_json(args.schema)
    compiled = compile_suite(suite, schema)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(compiled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "valid": True,
        "benchmarkId": compiled["benchmarkId"],
        "sourceStatus": compiled["sourceStatus"],
        "sourceHash": compiled["sourceHash"],
        "compiledHash": compiled["compiledHash"],
        "variantCount": compiled["variantCount"],
        "output": str(args.output) if args.output is not None else None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
