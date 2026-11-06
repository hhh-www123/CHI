"""Method-specific state application for the controlled A-D experiment.

These functions operate on isolated experiment copies. They never persist to the
application Store and never execute model code. Conversation orchestration and
task-oracle scoring remain responsibilities of the future formal runner.
"""

from __future__ import annotations

from copy import deepcopy

from fastapi import HTTPException

from backend.candidate_ranking import evaluate
from backend.patch_guard import decode_pointer, leaf_diff, validate_patch
from backend.trip_document import normalize_editor_state, to_trip_document, validate_document_schema


def _decision_result(current_key: str, current_state: dict, parsed_output: dict) -> dict | None:
    if parsed_output.get("kind") != "decision":
        return None
    return {
        "status": parsed_output["decision"],
        current_key: deepcopy(current_state),
        "changedPaths": [],
        "message": parsed_output["message"],
    }
def _parent(document: dict, path: str):
    tokens = decode_pointer(path)
    if not tokens:
        raise ValueError("root replacement is not supported")
    current = document
    for token in tokens[:-1]:
        if not isinstance(current, dict) or token not in current:
            raise ValueError(f"patch path does not exist: {path}")
        current = current[token]
    if not isinstance(current, dict) or tokens[-1] not in current:
        raise ValueError(f"patch path does not exist: {path}")
    return current, tokens[-1]


def apply_whole_document(current_document: dict, parsed_output: dict) -> dict:
    """Apply A/B output without preservation guards and expose the complete diff."""
    if parsed_output.get("kind") == "error":
        return {"status": "model_error", "document": deepcopy(current_document), "changedPaths": [], "error": deepcopy(parsed_output["error"])}
    decision = _decision_result("document", current_document, parsed_output)
    if decision is not None:
        return decision
    if parsed_output.get("kind") != "document":
        raise ValueError("A/B executor requires parsed document output")
    raw_draft = deepcopy(parsed_output["document"])
    validate_document_schema(raw_draft)
    raw_changes = leaf_diff(current_document, raw_draft)
    draft = deepcopy(raw_draft)
    overrides = []
    for field in ("tripId", "version"):
        if draft[field] != current_document[field]:
            overrides.append({"path": "/" + field, "modelValue": deepcopy(draft[field]), "systemValue": current_document[field] if field == "tripId" else current_document["version"] + 1})
    draft["tripId"] = current_document["tripId"]
    draft["version"] = current_document["version"] + 1
    before = deepcopy(current_document)
    before["version"] = draft["version"]
    return {
        "status": "applied",
        "document": draft,
        "changedPaths": leaf_diff(before, draft),
        "rawModelChangedPaths": raw_changes,
        "systemIdentityOverrides": overrides,
        "guardReport": {"schema": True, "pathAllowlist": False, "locks": False, "invariants": False, "version": False},
    }


def apply_allowlisted_patch(current_document: dict, parsed_output: dict) -> dict:
    """Apply C output with exact path authorization but no lock/invariant/version guard."""
    if parsed_output.get("kind") == "error":
        return {"status": "model_error", "document": deepcopy(current_document), "changedPaths": [], "error": deepcopy(parsed_output["error"])}
    decision = _decision_result("document", current_document, parsed_output)
    if decision is not None:
        return decision
    if parsed_output.get("kind") != "patch":
        raise ValueError("C executor requires parsed patch output")
    draft = deepcopy(current_document)
    for operation in parsed_output["patch"]:
        parent, key = _parent(draft, operation["path"])
        parent[key] = operation["value"]
    validate_document_schema(draft)
    business_changes = leaf_diff(current_document, draft)
    if not business_changes:
        return {
            "status": "no_change",
            "document": deepcopy(current_document),
            "changedPaths": [],
            "guardReport": {"schema": True, "pathAllowlist": True, "locks": False, "invariants": False, "version": False},
        }
    draft["version"] = current_document["version"] + 1
    before = deepcopy(current_document)
    before["version"] = draft["version"]
    changes = leaf_diff(before, draft)
    return {
        "status": "applied",
        "document": draft,
        "changedPaths": changes,
        "guardReport": {"schema": True, "pathAllowlist": True, "locks": False, "invariants": False, "version": False},
    }


def apply_guarded_candidates(
    session: dict,
    parsed_output: dict,
    *,
    selected_component_ids: list[str],
    fields: list[str],
    instruction: str,
    base_version: int,
) -> dict:
    """Validate/rank D candidates and apply the rules-v1 top candidate in memory."""
    if base_version != session.get("version"):
        return {
            "status": "version_conflict",
            "session": deepcopy(session),
            "changedPaths": [],
            "expectedVersion": base_version,
            "currentVersion": session.get("version"),
        }
    if parsed_output.get("kind") == "error":
        return {"status": "model_error", "session": deepcopy(session), "changedPaths": [], "error": deepcopy(parsed_output["error"])}
    decision = _decision_result("session", session, parsed_output)
    if decision is not None:
        return decision
    if parsed_output.get("kind") != "candidates":
        raise ValueError("D executor requires parsed candidates output")

    accepted = []
    rejected = []
    for candidate in parsed_output["candidates"]:
        try:
            checked = validate_patch(session, selected_component_ids, deepcopy(candidate["patch"]), fields)
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {"code": "PATCH_REJECTED", "message": str(error.detail)}
            rejected.append({"strategy": candidate["strategy"], "code": detail.get("code", "PATCH_REJECTED"), "message": detail.get("message", "rejected")})
            continue
        ranking = evaluate(checked, candidate["patch"], instruction)
        accepted.append({"strategy": candidate["strategy"], "patch": candidate["patch"], "checked": checked, "evaluation": ranking})

    if not accepted:
        return {"status": "guard_rejected", "session": deepcopy(session), "changedPaths": [], "acceptedCandidates": [], "rejectedCandidates": rejected}
    accepted.sort(key=lambda item: (-item["evaluation"]["score"], item["strategy"]))
    chosen = accepted[0]
    next_session = deepcopy(session)
    next_session.update(deepcopy(chosen["checked"]["payload"]))
    next_session["version"] = session["version"] + 1
    final_document = to_trip_document(next_session)
    expected_document = deepcopy(chosen["checked"]["draft"])
    expected_document["version"] = final_document["version"]
    if final_document != expected_document:
        raise AssertionError("guarded payload projection does not match the validated draft")
    return {
        "status": "applied",
        "session": next_session,
        "document": final_document,
        "changedPaths": deepcopy(chosen["checked"]["changedPaths"]),
        "derivedChangedPaths": deepcopy(chosen["checked"]["derivedChangedPaths"]),
        "chosenStrategy": chosen["strategy"],
        "selectionPolicy": "rules-v1-top1",
        "acceptedCandidates": [
            {"strategy": item["strategy"], "patch": deepcopy(item["patch"]), "evaluation": deepcopy(item["evaluation"])}
            for item in accepted
        ],
        "rejectedCandidates": rejected,
        "guardReport": {"schema": True, "pathAllowlist": True, "locks": True, "invariants": True, "version": True},
    }


def apply_trusted_setup(session: dict, actions: list[dict], *, enforce_guards: bool) -> dict:
    """Apply the same declared setup controls; only D persists/enforces guards."""
    if not isinstance(actions, list):
        raise ValueError("trusted setup actions must be a list")
    current = normalize_editor_state(session)
    next_session = deepcopy(current)
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("kind"), str):
            raise ValueError("invalid trusted setup action")
        kind = action["kind"]
        if kind == "set_max_budget":
            value = action.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 100 <= value <= 100000:
                raise ValueError("invalid max budget setup")
            if value < next_session["tripState"]["budgetLimit"]:
                raise ValueError("max budget setup is below the current document budget")
            if enforce_guards:
                hard = next_session["persistedConstraints"]["hardConstraints"]
                hard[:] = [item for item in hard if item.get("kind") != "max_budget"]
                hard.append({"id": "max-budget", "kind": "max_budget", "active": True, "value": value})
        elif kind == "lock_hotel":
            if set(action) != {"kind"}:
                raise ValueError("lock_hotel accepts no extra fields")
            if enforce_guards:
                next_session["controlState"]["hotelLocked"] = True
        elif kind == "lock_day":
            if set(action) != {"kind", "dayKey"} or not isinstance(action["dayKey"], str) or not action["dayKey"].startswith("day"):
                raise ValueError("lock_day requires dayKey")
            if enforce_guards:
                region = "day-" + action["dayKey"].removeprefix("day")
                if region not in next_session["controlState"]["lockedRegions"]:
                    next_session["controlState"]["lockedRegions"].append(region)
        else:
            raise ValueError(f"unknown trusted setup action: {kind}")
    if enforce_guards and actions:
        next_session["version"] = current["version"] + 1
    return {
        "session": next_session,
        "declaredControls": deepcopy(actions),
        "guardsEnforced": enforce_guards,
        "versionChanged": next_session["version"] != current["version"],
    }


def apply_d_tool_action(
    session: dict,
    parsed_output: dict,
    *,
    version_snapshots: dict[int, dict],
    selected_component_ids: list[str],
    fields: list[str],
    instruction: str,
) -> dict:
    """Execute D-only restore/constraint tools on an isolated session copy."""
    if parsed_output.get("kind") != "tool_action":
        raise ValueError("D tool executor requires a parsed tool_action")
    if parsed_output["decision"] == "update_constraint":
        constraint = parsed_output["constraint"]
        action = {"kind": "set_max_budget", "value": constraint["value"]}
        if constraint["value"] is None:
            next_session = normalize_editor_state(session)
            hard = next_session["persistedConstraints"]["hardConstraints"]
            hard[:] = [item for item in hard if item.get("kind") != "max_budget"]
            next_session["version"] = session["version"] + 1
            setup = {"session": next_session, "declaredControls": [deepcopy(action)], "guardsEnforced": True, "versionChanged": True}
        else:
            setup = apply_trusted_setup(session, [action], enforce_guards=True)
        return {
            "status": "constraint_updated",
            "session": setup["session"],
            "changedPaths": [],
            "message": parsed_output["message"],
            "constraint": deepcopy(constraint),
        }
    if parsed_output["decision"] != "restore":
        raise ValueError("unsupported D tool action")
    target_version = parsed_output["targetVersion"]
    if target_version not in version_snapshots:
        return {
            "status": "restore_target_missing",
            "session": deepcopy(session),
            "changedPaths": [],
            "targetVersion": target_version,
        }
    restored = normalize_editor_state(deepcopy(version_snapshots[target_version]))
    restored["id"] = session["id"]
    restored["version"] = session["version"] + 1
    before_document = to_trip_document(session)
    final_result = None
    if parsed_output["postRestoreCandidates"]:
        final_result = apply_guarded_candidates(
            restored,
            {"kind": "candidates", "candidates": deepcopy(parsed_output["postRestoreCandidates"])},
            selected_component_ids=selected_component_ids,
            fields=fields,
            instruction=instruction,
            base_version=restored["version"],
        )
        if final_result["status"] != "applied":
            return {**final_result, "status": "post_restore_" + final_result["status"], "restoreFromVersion": target_version}
        restored = final_result["session"]
    after_document = to_trip_document(restored)
    before_document["version"] = after_document["version"]
    return {
        "status": "restored",
        "session": restored,
        "document": after_document,
        "changedPaths": leaf_diff(before_document, after_document),
        "restoreFromVersion": target_version,
        "message": parsed_output["message"],
        "postRestore": final_result,
    }
