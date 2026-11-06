#!/usr/bin/env python3
"""Create a deterministic, unapproved packet for independent task review."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json


CHECKLIST = [
    {"field": "approved", "question": "该条件是否可进入冻结任务集？"},
    {"field": "setup_clear", "question": "setup/combined 指令是否清晰且只建立已声明目标？"},
    {"field": "probe_clear", "question": "probe 是否存在唯一可判定的行为与状态真值？"},
    {"field": "oracle_decidable", "question": "不看方法输出时，oracle 是否可由状态、目录和 rubric 独立判断？"},
    {"field": "distractors_neutral", "question": "全部 distractor 是否不引入、替换或暗示核心长期目标？"},
    {"field": "method_symmetric", "question": "任务文本、目标字段和目录事实是否不偏向 A–D 任一方法？"},
    {"field": "no_hidden_external_fact", "question": "判分是否不依赖冻结目录之外的旅游常识或实时事实？"},
    {"field": "severity_balanced", "question": "该条件失败的严重度是否与同模板其他长度保持可比？"},
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must not already exist")

    compiled = compile_suite(read_json(args.tasks), read_json(args.schema))
    tasks = []
    for variant in compiled["variants"]:
        tasks.append({
            "taskId": variant["taskId"],
            "templateId": variant["templateId"],
            "family": variant["family"],
            "title": variant["title"],
            "interactionLength": variant["interactionLength"],
            "goalDistance": variant["goalDistance"],
            "trustedSetupActions": deepcopy(variant["trustedSetupActions"]),
            "goals": deepcopy(variant["goals"]),
            "oracle": deepcopy(variant["oracle"]),
            "rounds": [{
                "round": item["round"],
                "phase": item["phase"],
                "instruction": item["instruction"],
                "target": deepcopy(item["target"]),
                "fields": deepcopy(item["fields"]),
                "expectedBehavior": item["expectedBehavior"],
                "candidateTargetCount": item["candidateTargetCount"],
                "sourceDistractorId": item.get("sourceDistractorId"),
                "goalEvents": deepcopy(item["goalEvents"]),
                "activeGoalIds": deepcopy(item["activeGoalIds"]),
                "turnGoal": deepcopy(item["turnGoal"]),
            } for item in variant["rounds"]],
        })
    packet = {
        "schemaVersion": "task-review-packet-1.0",
        "status": "unreviewed",
        "benchmarkId": compiled["benchmarkId"],
        "benchmarkSourceHash": compiled["sourceHash"],
        "benchmarkHash": compiled["compiledHash"],
        "taskCount": len(tasks),
        "checklist": CHECKLIST,
        "reviewRules": [
            "Two reviewers inspect the same packet independently before discussing disagreements.",
            "Each reviewer writes one CSV row per task and may not copy another reviewer's judgments.",
            "Any failed checklist item requires an issue_category and concrete notes; fixing a task creates a new compiled hash and invalidates prior approvals.",
            "This packet contains no approvals and cannot satisfy the readiness gate by itself.",
        ],
        "tasks": tasks,
    }
    packet["packetHash"] = canonical_hash(packet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(packet, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "created": True,
        "status": packet["status"],
        "taskCount": packet["taskCount"],
        "benchmarkHash": packet["benchmarkHash"],
        "packetHash": packet["packetHash"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
