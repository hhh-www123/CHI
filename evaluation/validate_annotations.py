#!/usr/bin/env python3
"""Validate blinded labels and report double-coding coverage/agreement."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

try:
    from .annotation_resolution import cohen_kappa, resolve_annotations, validate_annotation_schema
    from .prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from .task_suite import DEFAULT_SCHEMA as DEFAULT_TASK_SCHEMA, DEFAULT_SOURCE, compile_suite, read_json
except ImportError:  # direct script execution
    from annotation_resolution import cohen_kappa, resolve_annotations, validate_annotation_schema
    from prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from task_suite import DEFAULT_SCHEMA as DEFAULT_TASK_SCHEMA, DEFAULT_SOURCE, compile_suite, read_json


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "evaluation" / "annotations" / "schema.json"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--task-schema", type=Path, default=DEFAULT_TASK_SCHEMA)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--private-map", type=Path)
    args = parser.parse_args()
    if (args.packet is None) != (args.private_map is None):
        parser.error("--packet and --private-map must be provided together")
    data = read(args.annotations)
    schema = read(args.schema)
    validate_annotation_schema(data, schema)
    benchmark = compile_suite(read_json(args.benchmark), read_json(args.task_schema))
    prompts = load_bundle(args.prompt_dir)
    if data["benchmarkHash"] != benchmark["compiledHash"]:
        raise ValueError("annotation benchmarkHash does not match the selected benchmark")
    if data["promptHash"] != prompts["hash"]:
        raise ValueError("annotation promptHash does not match the selected prompt bundle")

    if args.packet is not None:
        _, stats = resolve_annotations(read(args.packet), read(args.private_map), data, schema)
        print(json.dumps({"valid": True, **stats}, ensure_ascii=False))
        return

    records_by_sample: dict[str, list[dict]] = defaultdict(list)
    identities: set[tuple[str, str]] = set()
    for record in data["records"]:
        identity = (record["sampleId"], record["annotatorId"])
        if identity in identities:
            raise ValueError(f"duplicate annotation record: {identity}")
        identities.add(identity)
        records_by_sample[record["sampleId"]].append(record)

    independent_by_sample = {
        sample_id: [record for record in records if record["role"] == "annotator"]
        for sample_id, records in records_by_sample.items()
    }
    double_samples = [records for records in independent_by_sample.values() if len(records) >= 2]
    pairs: list[tuple[bool, bool]] = []
    for records in double_samples:
        left, right = sorted(records, key=lambda item: item["annotatorId"])[:2]
        left_goals = {item["goalId"]: item["score"] for item in left["goalLabels"] if item["score"] is not None}
        right_goals = {item["goalId"]: item["score"] for item in right["goalLabels"] if item["score"] is not None}
        for goal_id in sorted(left_goals.keys() & right_goals.keys()):
            pairs.append((left_goals[goal_id] == 2, right_goals[goal_id] == 2))

    sample_count = len(records_by_sample)
    double_rate = len(double_samples) / sample_count if sample_count else 0
    kappa = cohen_kappa(pairs)
    blockers = []
    if data["status"] != "formal":
        blockers.append("annotation set is not marked formal")
    else:
        blockers.append("formal readiness validation requires --packet and --private-map")
    if sample_count == 0:
        blockers.append("annotation set is empty")
    if double_rate < 0.2:
        blockers.append("fewer than 20% of samples are double-coded")
    if len(pairs) < 20:
        blockers.append("fewer than 20 comparable double-coded goal labels")
    if kappa is None or kappa < 0.7:
        blockers.append("binary complete-goal Cohen kappa is missing or below 0.70")

    print(json.dumps({
        "valid": True,
        "formalReady": not blockers,
        "blockers": blockers,
        "recordCount": len(data["records"]),
        "sampleCount": sample_count,
        "annotatorCount": len({record["annotatorId"] for record in data["records"] if record["role"] == "annotator"}),
        "doubleCodedSamples": len(double_samples),
        "doubleCodedRate": double_rate,
        "comparableGoalLabels": len(pairs),
        "binaryCompleteGoalKappa": kappa,
        "labelCounts": dict(sorted(Counter(
            item["score"] for record in data["records"] for item in record["goalLabels"] if item["score"] is not None
        ).items())),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
