#!/usr/bin/env python3
"""Run a deterministic A-D orchestration pilot without a model or paper claims.

The fixture exercises shared history, declared controls, prompt rendering, strict
output parsing and method-specific state application on one description-only
task. It is not a baseline performance simulation and is always marked nonformal.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app import initial_payload
from backend.domain import session_catalog
from backend.trip_document import component_registry, normalize_editor_state, to_trip_document

try:
    from .adapters import parse_model_output, render_prompt
    from .model_client import ModelCallError
    from .prompt_bundle import DEFAULT_DIRECTORY, load_bundle
    from .state_executor import apply_allowlisted_patch, apply_d_tool_action, apply_guarded_candidates, apply_trusted_setup, apply_whole_document
    from .task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, compile_suite, read_json
except ImportError:  # direct script execution
    from adapters import parse_model_output, render_prompt
    from model_client import ModelCallError
    from prompt_bundle import DEFAULT_DIRECTORY, load_bundle
    from state_executor import apply_allowlisted_patch, apply_d_tool_action, apply_guarded_candidates, apply_trusted_setup, apply_whole_document
    from task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, compile_suite, read_json


ROOT = Path(__file__).resolve().parent.parent
METHODS = ("A", "B", "C", "D")


def make_session(method: str, episode_key: str, state_seed: str) -> dict:
    if method not in METHODS or not isinstance(episode_key, str) or not episode_key or not isinstance(state_seed, str) or not state_seed:
        raise ValueError("session identity inputs are invalid")
    payload = initial_payload()
    return normalize_editor_state({
        "id": hashlib.sha256(("experiment-fixture:" + state_seed).encode()).hexdigest()[:32],
        "version": 1,
        "history": [],
        "_undo": [],
        "_undoVersionIds": [],
        "_initialPayload": deepcopy(payload),
        **payload,
    })


def document_hash(document: dict) -> str:
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _round_oracle_metadata(round_item: dict, allowed_paths: list[str]) -> dict:
    return {
        "expectedBehavior": round_item["expectedBehavior"],
        "candidateTargetCount": round_item["candidateTargetCount"],
        "activeGoalIds": deepcopy(round_item["activeGoalIds"]),
        "goalEvents": deepcopy(round_item["goalEvents"]),
        "turnGoal": deepcopy(round_item["turnGoal"]),
        "allowedPaths": deepcopy(allowed_paths),
    }


def _attach_state_snapshots(record: dict, *, enabled: bool, before_document: dict | None, after_document: dict, session: dict) -> None:
    if not enabled:
        return
    if before_document is not None:
        record["beforeDocument"] = deepcopy(before_document)
    record["afterDocument"] = deepcopy(after_document)
    record["guardState"] = {
        "controlState": deepcopy(session["controlState"]),
        "persistedConstraints": deepcopy(session["persistedConstraints"]),
    }
    record["sessionFacts"] = {
        "hotelId": deepcopy(session["tripState"]["hotelId"]),
    }


def target_binding(session: dict, target: dict) -> dict:
    if target.get("kind") not in {"activity", "version"} or not target.get("dayKey") or not target.get("stopId"):
        raise ValueError("contract pilot supports activity targets only")
    return next(
        item for item in component_registry(session).values()
        if item["kind"] == "ActivityCard" and item["dayKey"] == target["dayKey"] and item["stopId"] == target["stopId"]
    )


def _place_options(document: dict, activity_id: str, catalog: dict, prefer_indoor: bool) -> list[dict]:
    activity = document["entities"]["activities"][activity_id]
    day = document["entities"]["days"][activity["dayId"]]
    used = {document["entities"]["activities"][item]["placeId"] for item in day["activityIds"]}
    options = [item for item in catalog["places"] if item["id"] not in used]
    if prefer_indoor:
        options = [item for item in options if item.get("indoor") is True]
    return options


def _edit_envelope(method: str, document: dict, path: str, values: list[str], catalog: dict, message: str) -> dict:
    activity_id = path.split("/")[3]
    field = path.rsplit("/", 1)[-1]
    if method in {"A", "B"}:
        draft = deepcopy(document)
        draft["entities"]["activities"][activity_id][field] = values[0]
        if field == "placeId":
            place = next(item for item in catalog["places"] if item["id"] == values[0])
            draft["entities"]["activities"][activity_id]["title"] = place["name"]
            draft["entities"]["activities"][activity_id]["description"] = place.get("description", "")
        return {"decision": "edit", "message": message, "document": draft}
    if method == "C":
        return {"decision": "edit", "message": message, "patch": [{"op": "replace", "path": path, "value": values[0]}]}
    candidates = []
    for strategy, value in zip(("minimal_change", "efficiency_first", "experience_first"), values):
        candidates.append({"strategy": strategy, "patch": [{"op": "replace", "path": path, "value": value}]})
    return {"decision": "edit", "message": message, "candidates": candidates}


def fixture_response(
    method: str,
    document: dict,
    paths: list[str],
    round_item: dict,
    catalog: dict,
    conversation: list[dict],
    restore_target_version: int,
) -> str:
    expected = round_item["expectedBehavior"]
    if expected in {"clarify", "reject"}:
        return json.dumps({"decision": expected, "message": f"fixture {expected}：只验证动作信封。"}, ensure_ascii=False)
    if expected == "update_goal":
        if method == "D":
            return json.dumps({
                "decision": "update_constraint",
                "message": "fixture 更新预算约束",
                "constraint": {"kind": "max_budget", "value": 3500, "confirmed": True},
            }, ensure_ascii=False)
        return json.dumps({"decision": "reject", "message": "fixture：该方法未注册持久约束更新工具。"}, ensure_ascii=False)
    if expected == "restore":
        if method != "D":
            return json.dumps({"decision": "reject", "message": "fixture：该方法未注册版本恢复工具。"}, ensure_ascii=False)
        description_paths = [path for path in paths if path.endswith("/description")]
        if len(description_paths) != 1:
            raise ValueError("restore fixture requires one post-restore description path")
        value = f"[contract fixture restore r{round_item['round']}] 恢复后局部修改。"
        candidate = {"strategy": "minimal_change", "patch": [{"op": "replace", "path": description_paths[0], "value": value}]}
        return json.dumps({
            "decision": "restore",
            "message": "fixture 恢复明确版本并执行局部增量",
            "targetVersion": restore_target_version,
            "postRestoreCandidates": [candidate],
        }, ensure_ascii=False)
    if expected != "edit" or len(paths) != 1:
        raise ValueError(f"unsupported fixture behavior: {expected}")

    path = paths[0]
    activity_id = path.split("/")[3]
    field = path.rsplit("/", 1)[-1]
    if field == "description":
        base = f"[contract fixture r{round_item['round']}] 只验证 A-D 编排，不代表模型语义质量。"
        values = [base + suffix for suffix in ("A", "B", "C")]
        message = "fixture 局部描述更新"
    elif field == "placeId":
        text = round_item["instruction"] + " " + " ".join(item["content"] for item in conversation if item["role"] == "user")
        options = _place_options(document, activity_id, catalog, any(token in text for token in ("室内", "怕雨", "雨天")))
        if not options:
            return json.dumps({"decision": "reject", "message": "fixture：没有符合目录条件的备选。"}, ensure_ascii=False)
        values = [item["id"] for item in options[:3]]
        while len(values) < 3:
            values.append(values[-1])
        message = "fixture 真实目录地点替换"
    else:
        raise ValueError(f"unsupported fixture field: {field}")
    return json.dumps(_edit_envelope(method, document, path, values, catalog, message), ensure_ascii=False)


def run_method(
    variant: dict,
    method: str,
    prompt_directory: Path,
    *,
    episode_key: str = "contract-pilot",
    response_provider=None,
    strict: bool = True,
    include_raw_response: bool = False,
    formal_experiment: bool = False,
    include_state_snapshots: bool = False,
    state_seed: str | None = None,
) -> dict:
    session = make_session(method, episode_key, state_seed or variant["initialFixture"])
    setup = apply_trusted_setup(session, variant["trustedSetupActions"], enforce_guards=method == "D")
    session = setup["session"]
    document = to_trip_document(session)
    baseline_budget = document["budgetLimit"]
    declared_controls = setup["declaredControls"]
    catalog = session_catalog(session)
    conversation: list[dict] = []
    versions = [{"round": 0, "version": document["version"], "summary": "initial", "documentHash": document_hash(document)}]
    version_snapshots = {session["version"]: deepcopy(session)}
    restore_target_version = session["version"]
    recorded_rounds = []

    for round_item in variant["rounds"]:
        history_only_setup = round_item["phase"] == "setup" and round_item["target"]["kind"] == "conversation"
        if round_item["phase"] == "setup" and (variant["trustedSetupActions"] or history_only_setup):
            acknowledgement = "已通过可信实验控件记录本轮约束。" if variant["trustedSetupActions"] else "已记录本轮对话偏好。"
            conversation.extend([
                {"role": "user", "content": round_item["instruction"]},
                {"role": "assistant", "content": acknowledgement},
            ])
            setup_record = {
                "round": round_item["round"],
                "phase": "setup",
                "status": "trusted_setup" if variant["trustedSetupActions"] else "history_setup",
                "instruction": round_item["instruction"],
                "changedPaths": [],
                "historyInputMessages": 0,
                "declaredControls": deepcopy(declared_controls),
                "guardsEnforced": method == "D",
                **_round_oracle_metadata(round_item, []),
            }
            _attach_state_snapshots(
                setup_record,
                enabled=include_state_snapshots,
                before_document=None,
                after_document=document,
                session=session,
            )
            recorded_rounds.append(setup_record)
            continue

        editable_fields = [field for field in round_item["fields"] if field != "none"]
        binding = target_binding(session, round_item["target"]) if editable_fields else None
        allowed_paths = [f"/entities/activities/{binding['entityId']}/{field}" for field in editable_fields] if binding else []
        if method == "D":
            document = to_trip_document(session)
        before_document = deepcopy(document)
        context = {
            "conversation_history": deepcopy(conversation),
            "current_document": document,
            "declared_controls": deepcopy(declared_controls),
            "version_history": deepcopy(versions),
            "catalog": catalog,
            "current_instruction": round_item["instruction"],
            "allowed_paths": allowed_paths,
            "locks_and_constraints": {
                "controlState": deepcopy(session["controlState"]),
                "persistedConstraints": deepcopy(session["persistedConstraints"]),
            },
        }
        prompt = render_prompt(prompt_directory, method, context)
        history_input_messages = len(conversation)
        try:
            if response_provider is None:
                raw_response = fixture_response(
                    method,
                    document,
                    allowed_paths,
                    round_item,
                    catalog,
                    conversation,
                    restore_target_version,
                )
                model_metadata = {"provider": "deterministic-contract-fixture", "fixture": True}
            else:
                call = response_provider(method=method, prompt=prompt, round_item=round_item)
                raw_response = call["text"]
                model_metadata = deepcopy(call.get("metadata", {}))
        except ModelCallError as error:
            if strict:
                raise
            conversation.extend([
                {"role": "user", "content": round_item["instruction"]},
                {"role": "assistant", "content": f"[模型调用失败：{error.code}]"},
            ])
            error_record = {
                "round": round_item["round"],
                "phase": round_item["phase"],
                "status": "model_error",
                "instruction": round_item["instruction"],
                "changedPaths": [],
                "historyInputMessages": history_input_messages,
                "promptHash": hashlib.sha256(prompt.encode()).hexdigest(),
                "error": error.metadata(),
                "beforeDocumentHash": document_hash(document),
                "afterDocumentHash": document_hash(document),
                **_round_oracle_metadata(round_item, allowed_paths),
            }
            _attach_state_snapshots(
                error_record,
                enabled=include_state_snapshots,
                before_document=before_document,
                after_document=document,
                session=session,
            )
            recorded_rounds.append(error_record)
            continue
        try:
            parsed = parse_model_output(raw_response, method, allowed_paths)
        except ValueError as error:
            if strict:
                raise
            conversation.extend([
                {"role": "user", "content": round_item["instruction"]},
                {"role": "assistant", "content": "[模型输出不符合方法契约]"},
            ])
            invalid_record = {
                "round": round_item["round"],
                "phase": round_item["phase"],
                "status": "invalid_output",
                "instruction": round_item["instruction"],
                "changedPaths": [],
                "historyInputMessages": history_input_messages,
                "promptHash": hashlib.sha256(prompt.encode()).hexdigest(),
                "rawResponseHash": hashlib.sha256(raw_response.encode()).hexdigest(),
                "modelMetadata": model_metadata,
                "error": {"code": "OUTPUT_CONTRACT_INVALID", "message": str(error)},
                "beforeDocumentHash": document_hash(document),
                "afterDocumentHash": document_hash(document),
                **_round_oracle_metadata(round_item, allowed_paths),
            }
            if include_raw_response:
                invalid_record["rawResponse"] = raw_response
            _attach_state_snapshots(
                invalid_record,
                enabled=include_state_snapshots,
                before_document=before_document,
                after_document=document,
                session=session,
            )
            recorded_rounds.append(invalid_record)
            continue
        before_hash = document_hash(document)
        try:
            if method in {"A", "B"}:
                result = apply_whole_document(document, parsed)
                document = result["document"]
            elif method == "C":
                result = apply_allowlisted_patch(document, parsed)
                document = result["document"]
            else:
                selected = [binding["componentId"]] if binding else []
                if parsed["kind"] == "tool_action":
                    result = apply_d_tool_action(
                        session,
                        parsed,
                        version_snapshots=version_snapshots,
                        selected_component_ids=selected,
                        fields=editable_fields,
                        instruction=round_item["instruction"],
                    )
                else:
                    result = apply_guarded_candidates(
                        session,
                        parsed,
                        selected_component_ids=selected,
                        fields=editable_fields,
                        instruction=round_item["instruction"],
                        base_version=session["version"],
                    )
                session = result["session"]
                document = to_trip_document(session)
                version_snapshots[session["version"]] = deepcopy(session)
        except ValueError as error:
            if strict:
                raise
            result = {
                "status": "state_rejected",
                "changedPaths": [],
                "error": {"code": "STATE_APPLICATION_INVALID", "message": str(error)},
            }
        if result["status"] not in {"applied", "clarify", "reject", "constraint_updated", "restored"}:
            if strict:
                raise RuntimeError(f"fixture contract application failed: {method}/{result['status']}")
        conversation.extend([
            {"role": "user", "content": round_item["instruction"]},
            {"role": "assistant", "content": parsed.get("message", f"[{result['status']}]")},
        ])
        if result["status"] in {"applied", "constraint_updated", "restored"}:
            versions.append({
                "round": round_item["round"],
                "version": document["version"],
                "summary": parsed.get("message", result["status"]),
                "documentHash": document_hash(document),
            })
        round_record = {
            "round": round_item["round"],
            "phase": round_item["phase"],
            "status": result["status"],
            "instruction": round_item["instruction"],
            "changedPaths": result["changedPaths"],
            "historyInputMessages": history_input_messages,
            "promptHash": hashlib.sha256(prompt.encode()).hexdigest(),
            "rawResponseHash": hashlib.sha256(raw_response.encode()).hexdigest(),
            "modelMetadata": model_metadata,
            "beforeDocumentHash": before_hash,
            "afterDocumentHash": document_hash(document),
            **_round_oracle_metadata(round_item, allowed_paths),
        }
        application_fields = (
            "guardReport", "rawModelChangedPaths", "systemIdentityOverrides",
            "derivedChangedPaths", "chosenStrategy", "selectionPolicy",
            "acceptedCandidates", "rejectedCandidates", "restoreFromVersion",
            "constraint",
        )
        application = {key: deepcopy(result[key]) for key in application_fields if key in result}
        if application:
            round_record["application"] = application
        if result.get("error") is not None:
            round_record["error"] = deepcopy(result["error"])
        if include_raw_response:
            round_record["rawResponse"] = raw_response
        _attach_state_snapshots(
            round_record,
            enabled=include_state_snapshots,
            before_document=before_document,
            after_document=document,
            session=session,
        )
        recorded_rounds.append(round_record)

    episode = {
        "method": method,
        "taskId": variant["taskId"],
        "fixture": response_provider is None,
        "formalExperiment": formal_experiment,
        "completed": len(recorded_rounds) == variant["interactionLength"],
        "rounds": recorded_rounds,
        "finalDocumentHash": document_hash(document),
        "protectedBudgetPreserved": document["budgetLimit"] == baseline_budget,
        "historyMessages": len(conversation),
    }
    if include_state_snapshots:
        episode["finalDocument"] = deepcopy(document)
        episode["catalog"] = deepcopy(catalog)
        episode["declaredControls"] = deepcopy(declared_controls)
        episode["finalGuardState"] = {
            "controlState": deepcopy(session["controlState"]),
            "persistedConstraints": deepcopy(session["persistedConstraints"]),
        }
    return episode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default="retention-budget-hotel-L5")
    parser.add_argument("--all-families", action="store_true")
    parser.add_argument("--all-conditions", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    compiled = compile_suite(read_json(DEFAULT_SOURCE), read_json(DEFAULT_SCHEMA))
    variants = {item["taskId"]: item for item in compiled["variants"]}
    if args.all_families and args.all_conditions:
        parser.error("choose only one of --all-families or --all-conditions")
    if args.all_conditions:
        selected_variants = compiled["variants"]
    elif args.all_families:
        selected_variants = [item for item in compiled["variants"] if item["interactionLength"] == 5]
    else:
        if args.task_id not in variants:
            parser.error("unknown --task-id")
        selected_variants = [variants[args.task_id]]
    prompt_bundle = load_bundle(args.prompt_dir)
    episodes = [run_method(variant, method, args.prompt_dir) for variant in selected_variants for method in METHODS]
    output = {
        "schemaVersion": "contract-pilot-1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "formalExperiment": False,
        "fixture": True,
        "notes": [
            "This pilot validates A-D orchestration contracts only.",
            "The deterministic fixture is not a model and cannot be used for method-quality comparisons.",
        ],
        "benchmarkHash": compiled["compiledHash"],
        "benchmarkSourceHash": compiled["sourceHash"],
        "promptHash": prompt_bundle["hash"],
        "taskIds": [item["taskId"] for item in selected_variants],
        "episodes": episodes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": all(item["completed"] and item["protectedBudgetPreserved"] for item in episodes),
        "methods": list(METHODS),
        "tasks": len(selected_variants),
        "episodes": len(episodes),
        "rounds": sum(len(item["rounds"]) for item in episodes),
        "formalExperiment": False,
        "fixture": True,
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
