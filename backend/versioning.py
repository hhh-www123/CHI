"""Immutable version records in the existing SQLite transaction, without secrets."""
from copy import deepcopy
from datetime import datetime, timezone

from .patch_guard import leaf_diff
from .trip_document import normalize_editor_state, to_trip_document

SNAPSHOT_KEYS = ("tripState", "uiSpec", "controlState", "routeData", "catalogVersion", "catalog",
                 "planningSettings", "planningWarnings", "editorMetadata", "persistedConstraints", "presentationSettings")


def payload_snapshot(session):
    value = normalize_editor_state(session) if "tripState" in session else deepcopy(session)
    value.setdefault("presentationSettings", {"showBudgetDetails": True})
    return deepcopy({key: value[key] for key in SNAPSHOT_KEYS if key in value})


def snapshot_document(session):
    if "tripState" not in session:
        return None
    return to_trip_document(session)


def _difference(before, after, path=""):
    if type(before) is type(after) and isinstance(before, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                result.append({"path": child, "before": deepcopy(before.get(key)), "after": deepcopy(after.get(key))})
            else:
                result.extend(_difference(before[key], after[key], child))
        return result
    if type(before) is type(after) and isinstance(before, list):
        result = []
        for index in range(max(len(before), len(after))):
            left, right = (before[index] if index < len(before) else None), (after[index] if index < len(after) else None)
            result.extend(_difference(left, right, path + "/" + str(index)))
        return result
    return [] if type(before) is type(after) and before == after else [{"path": path, "before": deepcopy(before), "after": deepcopy(after)}]



def make_record(previous, current, *, summary=None, kind=None, audit=None, restore_from=None, legacy=False):
    """Build metadata from trusted operation handlers, never from client fields."""
    payload = payload_snapshot(current)
    old = payload_snapshot(previous) if previous else None
    latest = current.get("history", [{}])[-1] if current.get("history") else {}
    kind = kind or latest.get("kind", "checkpoint")
    audit = deepcopy(audit or {})
    changes = [entry["path"] for entry in _difference(old, payload)] if old else []
    document = snapshot_document(current)
    document_changes = []
    if previous and document is not None:
        before_document = snapshot_document(previous)
        before_document["version"] = document["version"]
        document_changes = leaf_diff(before_document, document)
    return {
        "tripId": current["id"], "version": current["version"],
        "parentVersion": previous["version"] if previous else None,
        "baseVersion": previous["version"] if previous else current["version"],
        "createdAt": datetime.now(timezone.utc).isoformat(), "kind": kind,
        "summary": summary or latest.get("summary", "旧行程当前版本接入检查点"),
        "userRequest": audit.get("userRequest", summary or latest.get("summary", "当前状态检查点")),
        "selectedComponentIds": deepcopy(audit.get("selectedComponentIds", [])),
        "candidateId": audit.get("candidateId"), "patch": deepcopy(audit.get("patch", [])),
        "evaluation": deepcopy(audit.get("evaluation")),
        "allowedPaths": deepcopy(audit.get("allowedPaths", [])), "derivedChangedPaths": deepcopy(audit.get("derivedChangedPaths", [])),
        "pathNamespaces": {"allowedPaths": audit.get("pathNamespace", "payloadSnapshot"), "changedPaths": "TripDocument" if kind in {"candidate_commit", "restricted_patch"} else "payloadSnapshot", "payloadChangedPaths": "payloadSnapshot", "documentChangedPaths": "TripDocument"},
        "changedPaths": deepcopy(audit.get("changedPaths", document_changes) if kind in {"candidate_commit", "restricted_patch"} else changes),
        "payloadChangedPaths": changes, "documentChangedPaths": document_changes,
        "documentSnapshot": document, "payloadSnapshot": payload,
        "constraintsSnapshot": deepcopy(payload.get("persistedConstraints", {})),
        "validationReport": deepcopy(audit.get("validationReport", {"valid": True, "source": "trusted_user_action"})),
        "restoreFromVersion": restore_from, "legacySnapshot": legacy,
        "authorization": audit.get("authorization", "explicit_user_action"),
    }


def checkpoint(session):
    result = make_record(None, session, kind="checkpoint", summary="当前状态接入持久版本记录（不重建丢失历史）")
    result["validationReport"] = {"valid": True, "source": "existing_state_checkpoint", "historicalValidationReplayed": False}
    return result
