#!/usr/bin/env python3
"""Run an isolated, deterministic 10-round evaluation pilot.

This verifies the evaluation contract and the current guarded candidate path. It
uses demo/mock mode, no real model or map provider, and must not be presented as
the formal A-D experiment or as evidence of semantic model quality.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def encode_pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def leaf_diff(before, after, parts: tuple[str, ...] = ()) -> list[str]:
    if type(before) is not type(after):
        return [encode_pointer(parts)]
    if isinstance(before, dict):
        result: list[str] = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                result.append(encode_pointer(parts + (key,)))
            else:
                result.extend(leaf_diff(before[key], after[key], parts + (key,)))
        return result
    if isinstance(before, list):
        return [] if before == after else [encode_pointer(parts)]
    return [] if before == after else [encode_pointer(parts)]


def decode_pointer(path: str) -> tuple[str, ...]:
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/"))


def pointer_get(root, path: str):
    current = root
    for token in decode_pointer(path):
        current = current[token]
    return current


def normalized_signature(document: dict) -> str:
    value = deepcopy(document)
    value["version"] = 0
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def time_order_satisfied(document: dict) -> bool:
    activities = document["entities"]["activities"]
    for day in document["entities"]["days"].values():
        times = [activities[activity_id]["startTime"] for activity_id in day["activityIds"]]
        if times != sorted(set(times)):
            return False
    return True


def response_json(response, expected: int = 200) -> dict:
    if response.status_code != expected:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def activity_binding(session: dict, day_key: str, stop_id: str) -> dict:
    for binding in session["componentRegistry"].values():
        if binding.get("kind") == "ActivityCard" and binding.get("dayKey") == day_key and binding.get("stopId") == stop_id:
            return binding
    raise RuntimeError(f"missing activity binding: {day_key}/{stop_id}")


def constraint_state(session: dict) -> list[dict]:
    hard = session["persistedConstraints"]["hardConstraints"]
    max_budget = next((item for item in hard if item.get("kind") == "max_budget" and item.get("active") is True), None)
    return [
        {"id": "time-order", "introducedRound": 0, "weight": 2, "satisfied": time_order_satisfied(session["tripDocument"])},
        {
            "id": "max-budget-3000",
            "introducedRound": 0,
            "weight": 2,
            "satisfied": bool(max_budget and max_budget.get("value") == 3000 and session["tripDocument"]["budgetLimit"] <= 3000),
        },
    ]


STEPS = [
    {"dayKey": "day1", "stopId": "day1-stop-1", "field": "startTime", "fields": ["startTime"], "candidateTargetCount": 1, "instruction": "将开始时间改为10:00", "expected": "10:00"},
    {"dayKey": "day2", "stopId": "day2-stop-2", "field": "startTime", "fields": ["startTime"], "candidateTargetCount": 1, "instruction": "将开始时间改为14:00", "expected": "14:00"},
    {"dayKey": "day2", "stopId": "day2-stop-2", "field": "description", "fields": ["description"], "candidateTargetCount": 1, "instruction": "安排轻松一些，只改描述"},
    {"dayKey": "day3", "stopId": "day3-stop-1", "field": "placeId", "fields": ["placeId"], "candidateTargetCount": 3, "instruction": "换个景点，保留时间和其他内容"},
    {"dayKey": "day3", "stopId": "day3-stop-2", "fields": ["description", "startTime"], "candidateTargetCount": 3, "instruction": "安排轻松一些"},
    {"dayKey": "day1", "stopId": "day1-stop-3", "field": "startTime", "fields": ["startTime"], "candidateTargetCount": 1, "instruction": "将开始时间改为16:30", "expected": "16:30"},
    {"dayKey": "day2", "stopId": "day2-stop-1", "field": "placeId", "fields": ["placeId"], "candidateTargetCount": 3, "instruction": "换个景点，保留时间和其他内容"},
    {"dayKey": "day1", "stopId": "day1-stop-2", "fields": ["description", "startTime"], "candidateTargetCount": 3, "instruction": "安排轻松一些"},
    {"dayKey": "day1", "stopId": "day1-stop-1", "field": "startTime", "fields": ["startTime"], "candidateTargetCount": 1, "instruction": "将开始时间改为10:15", "expected": "10:15"},
    {"dayKey": "day2", "stopId": "day2-stop-3", "fields": ["description", "startTime"], "candidateTargetCount": 3, "instruction": "安排轻松一些"},
]


def run_episode(client, trial: int, payload_snapshot) -> dict:
    session = response_json(client.post("/api/sessions", json={}))
    session_id = session["id"]
    session = response_json(client.post(
        f"/api/sessions/{session_id}/editor/constraints",
        json={"baseVersion": session["version"], "confirmConstraintChange": True, "maxBudget": 3000},
    ))
    session = response_json(client.patch(
        f"/api/sessions/{session_id}/locks",
        json={"baseVersion": session["version"], "target": "hotel", "locked": True},
    ))
    baseline_hotel = session["tripState"]["hotelId"]
    baseline_budget = session["tripDocument"]["budgetLimit"]
    active: dict[str, dict] = {
        "global:budget": {
            "id": "preserve-budget-input",
            "introducedRound": 0,
            "weight": 2,
            "source": "document",
            "path": "/budgetLimit",
            "expected": baseline_budget,
        },
        "global:hotel": {
            "id": "preserve-hotel-selection",
            "introducedRound": 0,
            "weight": 2,
            "source": "session",
            "path": "/tripState/hotelId",
            "expected": baseline_hotel,
        },
        "global:hotel-lock": {
            "id": "preserve-hotel-lock",
            "introducedRound": 0,
            "weight": 2,
            "source": "session",
            "path": "/controlState/hotelLocked",
            "expected": True,
        },
    }
    rounds: list[dict] = []

    for round_number, step in enumerate(STEPS, 1):
        binding = activity_binding(session, step["dayKey"], step["stopId"])
        activity_id = binding["entityId"]
        target_path = f"/entities/activities/{activity_id}/{step['field']}" if "field" in step else None
        before = deepcopy(session["tripDocument"])
        started = time.perf_counter()
        batch = response_json(client.post(
            f"/api/sessions/{session_id}/edits/candidates",
            headers={"Accept": "application/json"},
            json={
                "baseVersion": session["version"],
                "selectedComponentIds": [binding["componentId"]],
                "instruction": step["instruction"],
                "agentMode": "mock",
                "fields": step["fields"],
            },
        ))
        chosen = min(batch["candidates"], key=lambda item: item.get("evaluation", {}).get("rank", 999))
        session = response_json(client.post(
            f"/api/sessions/{session_id}/edits/commit",
            json={"baseVersion": session["version"], "candidateId": chosen["candidateId"]},
        ))
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        after = deepcopy(session["tripDocument"])
        before["version"] = after["version"]
        actual_paths = leaf_diff(before, after)
        if sorted(actual_paths) != sorted(chosen["changedPaths"]):
            raise RuntimeError("committed TripDocument diff does not match the validated candidate")
        derived_paths = set(chosen.get("derivedChangedPaths", []))
        direct_paths = sorted(path for path in actual_paths if path not in derived_paths)
        if not direct_paths:
            raise RuntimeError(f"round {round_number} produced no direct business change")
        desired_paths = sorted(chosen["changedPaths"] if target_path is None else {target_path, *derived_paths})
        unauthorized_paths = sorted(path for path in actual_paths if path not in desired_paths)
        adopted_value = pointer_get(after, target_path) if target_path is not None else None
        if "expected" in step and adopted_value != step["expected"]:
            raise RuntimeError(f"explicit target not achieved at round {round_number}")

        retired: list[dict] = []
        goal_paths = [target_path] if target_path is not None else direct_paths
        for goal_path in goal_paths:
            old = active.pop("path:" + goal_path, None)
            if old is not None:
                retired.append({
                    "id": old["id"],
                    "leaked": pointer_get(after, goal_path) == old["expected"],
                })
            field = goal_path.rsplit("/", 1)[-1]
            active["path:" + goal_path] = {
                "id": f"round-{round_number}:{field}:{step['dayKey']}:{step['stopId']}",
                "introducedRound": round_number,
                "weight": 1,
                "source": "document",
                "path": goal_path,
                "expected": pointer_get(after, goal_path),
            }

        active_goals = []
        for goal in active.values():
            source = after if goal["source"] == "document" else session
            active_goals.append({
                "id": goal["id"],
                "introducedRound": goal["introducedRound"],
                "weight": goal["weight"],
                "satisfied": pointer_get(source, goal["path"]) == goal["expected"],
            })
        constraints = constraint_state(session)
        candidates = []
        for candidate in batch["candidates"]:
            if target_path is not None:
                candidate_value = pointer_get(candidate["document"], target_path)
                goal_coverage = 1 if candidate_value != pointer_get(before, target_path) else 0
            else:
                candidate_derived = set(candidate.get("derivedChangedPaths", []))
                candidate_direct = [path for path in candidate["changedPaths"] if path not in candidate_derived]
                goal_coverage = 1 if candidate_direct else 0
            candidates.append({
                "valid": True,
                "outcomeSignature": normalized_signature(candidate["document"]),
                "goalCoverage": goal_coverage,
                "systemRank": candidate.get("evaluation", {}).get("rank"),
            })
        proposed_count = batch.get("metrics", {}).get("proposed")
        if not isinstance(proposed_count, int) or proposed_count < batch["candidateCount"]:
            raise RuntimeError("candidate proposal counts are inconsistent")
        rejected_proposals = [
            item for item in batch.get("rejectedCandidates", [])
            if item.get("code") != "NO_STRATEGY_PROPOSAL"
        ]
        if len(rejected_proposals) != proposed_count - batch["candidateCount"]:
            raise RuntimeError("candidate rejection diagnostics do not match actual proposals")
        candidates.extend({"valid": False, "rejectionCode": item.get("code")} for item in rejected_proposals)
        success = (
            not unauthorized_paths
            and all(goal["satisfied"] for goal in active_goals)
            and all(constraint["satisfied"] for constraint in constraints)
            and (target_path is None or pointer_get(after, target_path) == adopted_value)
        )
        rounds.append({
            "round": round_number,
            "instruction": step["instruction"],
            "selectedComponentId": binding["componentId"],
            "activeGoals": active_goals,
            "retiredGoals": retired,
            "activeConstraints": constraints,
            "desiredPaths": desired_paths,
            "actualChangedPaths": sorted(actual_paths),
            "unauthorizedChangedPaths": unauthorized_paths,
            "latencyMs": latency_ms,
            "generationMode": batch["generationMode"],
            "candidateCount": batch["candidateCount"],
            "candidateTargetCount": step["candidateTargetCount"],
            "candidates": candidates,
            "success": success,
        })

    recovery_baseline = deepcopy(session)
    recovery_target_version = session["version"]
    recovery_binding = activity_binding(session, "day2", "day2-stop-3")
    recovery_path = f"/entities/activities/{recovery_binding['entityId']}/description"
    preview = response_json(client.post(
        f"/api/sessions/{session_id}/editor/preview",
        json={
            "baseVersion": session["version"],
            "selectedComponentId": recovery_binding["componentId"],
            "fields": ["description"],
            "patch": [{"op": "replace", "path": recovery_path, "value": "[评测注入错误] 此描述应由历史恢复撤销。"}],
        },
    ))
    session = response_json(client.post(
        f"/api/sessions/{session_id}/editor/apply",
        json={"baseVersion": session["version"], "previewId": preview["previewId"]},
    ))
    detected = pointer_get(session["tripDocument"], recovery_path) != pointer_get(recovery_baseline["tripDocument"], recovery_path)
    recovery_started = time.perf_counter()
    session = response_json(client.post(
        f"/api/sessions/{session_id}/versions/restore",
        json={"baseVersion": session["version"], "targetVersion": recovery_target_version, "confirmRestore": True},
    ))
    recovery_elapsed_ms = round((time.perf_counter() - recovery_started) * 1000, 3)
    residual_errors = len(leaf_diff(payload_snapshot(recovery_baseline), payload_snapshot(session)))
    recovery = {
        "seededAtRound": len(STEPS),
        "detectedAtRound": len(STEPS) if detected else None,
        "recoveredAtRound": len(STEPS) + 1 if residual_errors == 0 else None,
        "residualErrors": residual_errors,
        "clicks": 2,
        "elapsedMs": recovery_elapsed_ms,
        "injectedPath": recovery_path,
        "restoreFromVersion": recovery_target_version,
    }

    return {
        "method": "D-mock-pilot",
        "taskId": "ten-round-scoped-editing",
        "trial": trial,
        "success": all(round_item["success"] for round_item in rounds) and detected and residual_errors == 0,
        "rounds": rounds,
        "recovery": recovery,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("--trials must be between 1 and 20")

    with tempfile.TemporaryDirectory(prefix="travel-genui-evaluation-pilot-") as temporary:
        root = Path(temporary)
        os.environ.update({
            "TRAVEL_ENV_FILE": str(root / "absent.env"),
            "TRAVEL_GENUI_DATA_DIR": str(root / "data"),
            "AGENT_MODE": "demo",
            "CANDIDATE_AGENT_MODE": "mock",
            "CANDIDATE_TTL_SECONDS": "600",
            "LLM_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "AMAP_KEY": "",
            "AGENT_REQUESTS_PER_MINUTE": "120",
        })
        from fastapi.testclient import TestClient
        from backend import access
        from backend import app as module
        from backend.versioning import payload_snapshot

        access._buckets.clear()
        module.store = module.Store(root / "data")
        with TestClient(module.app) as client:
            episodes = [run_episode(client, trial, payload_snapshot) for trial in range(1, args.trials + 1)]
        dataset = {
            "schemaVersion": "1.0",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "pilot": True,
            "formalExperiment": False,
            "generationMode": "mock",
            "realModel": False,
            "realMap": False,
            "method": "D-mock-pilot",
            "appVersion": module.APP_VERSION,
            "serverRevision": module.SERVER_REVISION,
            "notes": [
                "This pilot validates the metric pipeline and current guarded commit path only.",
                "For open-ended description and place requests, adopting a validated candidate defines the intended value; no human semantic rating is claimed.",
                "Recovery uses a controlled error in the isolated test session followed by the real version-restore endpoint.",
                "A-D comparison, real-model variance, user ratings and appropriate reliance are not measured here.",
            ],
            "episodes": episodes,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "passed": all(item["success"] for item in episodes),
            "episodes": len(episodes),
            "rounds": sum(len(item["rounds"]) for item in episodes),
            "output": str(args.output),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
