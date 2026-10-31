"""Restricted replace-only JSON Patch validation on cloned TripDocument values.

Only server-generated bindings grant leaf access. Preview and validation never
write official state. Neither AG-UI events nor client/Agent allowedPaths are
accepted as authorisation inputs.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from .domain import session_catalog
from .trip_document import (
    TIME_PATTERN, component_is_locked, component_registry, deny, locked_component_ids,
    normalize_editor_state, period_for_time, to_trip_document, validate_document_schema,
    validate_locked_unchanged,
)

EDITABLE_FIELDS = ("description", "startTime", "placeId")
DEFAULT_EDITABLE_FIELDS = ("description", "startTime")
MAX_PATCH_OPERATIONS = 16
MAX_PATCH_BYTES = 16000
_DANGEROUS_SEGMENTS = {
    "__proto__", "prototype", "constructor", "__class__", "__dict__",
    "__bases__", "__subclasses__",
}


def decode_pointer(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        deny("INVALID_REQUEST", "仅接受非根路径的标准 JSON Pointer。")
    if any(ord(char) < 32 for char in path) or path.startswith("/#"):
        deny("INVALID_REQUEST", "JSON Pointer 不能包含控制字符或 URI fragment。")
    result = []
    for raw in path[1:].split("/"):
        if not raw or re.search(r"~(?![01])", raw):
            deny("INVALID_REQUEST", "JSON Pointer 包含空路径段或错误转义。")
        token = raw.replace("~1", "/").replace("~0", "~")
        if token in _DANGEROUS_SEGMENTS or token.startswith("_"):
            deny("PATCH_OUT_OF_SCOPE", "不允许访问危险属性名或内部字段。")
        if token in {".", "..", "-"} or "%" in token:
            deny("PATCH_OUT_OF_SCOPE", "不允许路径跳转、数组追加或 URL 编码路径。")
        result.append(token)
    canonical = "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in result)
    if canonical != path:
        deny("INVALID_REQUEST", "JSON Pointer 不是规范形式。")
    return tuple(result)


def encode_pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in parts)


def resolve_scope(session: dict, selected_component_ids: list[str], fields: list[str] | None = None) -> dict:
    if not isinstance(selected_component_ids, list) or not selected_component_ids:
        deny("NO_SELECTION", "请先在编辑模式中选择局部组件。")
    if len(selected_component_ids) != 1 or not isinstance(selected_component_ids[0], str):
        deny("INVALID_REQUEST", "请一次选择一个日期、时段或景点组件。")
    if fields is not None and (not isinstance(fields, list) or any(not isinstance(item, str) for item in fields)):
        deny("INVALID_REQUEST", "字段选择必须是受支持字段组成的字符串列表。")
    chosen_fields = list(DEFAULT_EDITABLE_FIELDS if fields is None else fields)
    if not chosen_fields or len(chosen_fields) != len(set(chosen_fields)) or any(field not in EDITABLE_FIELDS for field in chosen_fields):
        deny("INVALID_REQUEST", "只能选择景点描述、开始时间或真实地点字段。")
    value = normalize_editor_state(session)
    registry = component_registry(value)
    selected_id = selected_component_ids[0]
    if selected_id not in registry:
        deny("SCOPE_DENIED", "选区不在当前会话的组件注册表中。", 403)
    selected = registry[selected_id]
    locked = locked_component_ids(value, registry)
    selected_locked = component_is_locked(selected_id, registry, locked)
    eligible = []
    allowed = []
    for cid, binding in registry.items():
        if binding["kind"] != "ActivityCard":
            continue
        # Parent links, not path prefixes, establish containment.
        current = cid
        inside = False
        while current:
            if current == selected_id:
                inside = True
                break
            current = registry[current].get("parentId")
        if not inside or selected_locked or component_is_locked(cid, registry, locked):
            continue
        eligible.append(binding["entityId"])
        allowed.extend(path for path in binding["editablePaths"] if path.rsplit("/", 1)[-1] in chosen_fields)
    return {
        "phase": "Scoped editing", "selectedComponentIds": [selected_id],
        "allowedPaths": sorted(allowed), "lockedComponentIds": locked,
        "editableFields": chosen_fields, "eligibleActivityIds": eligible,
        "labels": [selected["label"]], "selectedLocked": selected_locked,
        "restriction": "可修改当前选中日期、时段或景点内未锁定的描述、开始时间及真实地点；其他内容保持保护。",
    }


def _get(root: dict, tokens: tuple[str, ...]):
    current = root
    for token in tokens:
        if not isinstance(current, dict) or token not in current:
            deny("INVALID_REQUEST", "replace 目标必须是已存在的对象字段。")
        current = current[token]
    return current


def leaf_diff(before, after, parts: tuple[str, ...] = ()) -> list[str]:
    if type(before) is not type(after):
        return [encode_pointer(parts)]
    if isinstance(before, dict):
        changes = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                changes.append(encode_pointer(parts + (key,)))
            else:
                changes.extend(leaf_diff(before[key], after[key], parts + (key,)))
        return changes
    # Orders/arrays are never writable by the activity editor; treat their whole value as a leaf.
    return [] if before == after else [encode_pointer(parts)]


def _assert_json(value, depth: int = 0):
    if depth > 8:
        deny("INVALID_REQUEST", "补丁嵌套过深。")
    if isinstance(value, float) and not math.isfinite(value):
        deny("INVALID_REQUEST", "JSON 不接受 NaN 或 Infinity。")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key in _DANGEROUS_SEGMENTS or key.startswith("_"):
                deny("PATCH_OUT_OF_SCOPE", "值中不允许危险属性名或内部字段。")
            _assert_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _assert_json(child, depth + 1)
    elif value is not None and type(value) not in {str, int, float, bool}:
        deny("INVALID_REQUEST", "补丁必须由可序列化的 JSON 值组成。")


def validate_invariants(document: dict, constraints: dict) -> None:
    days = document["entities"]["days"]
    activities = document["entities"]["activities"]
    for day in days.values():
        times = [activities[aid]["startTime"] for aid in day["activityIds"]]
        if times != sorted(set(times)):
            deny("INVARIANT_VIOLATION", "每天开始时间必须严格递增且不能重复。")
    for constraint in constraints["hardConstraints"]:
        if not isinstance(constraint, dict) or type(constraint.get("active")) is not bool:
            deny("SCHEMA_INVALID", "持久硬约束结构无效。")
        if not constraint["active"]:
            continue
        kind = constraint.get("kind")
        if kind in {"time_order", "immutable_subtree"}:
            continue
        if kind == "max_budget":
            ceiling = constraint.get("value")
            if type(ceiling) not in {int, float} or not math.isfinite(ceiling) or document["budgetLimit"] > ceiling:
                deny("INVARIANT_VIOLATION", "总预算超出了已保存的预算硬约束。")
        else:
            deny("INVARIANT_VIOLATION", "无法执行该已启用硬约束，拒绝绕过。")


def project_back(session: dict, draft: dict, changed_paths: list[str]) -> dict:
    """Map authorised changed leaves to legacy data; never return identity/version.

    The application can pass this payload into its existing versioned commit.
    Nothing is mutated here, including on failure.
    """
    value = normalize_editor_state(session)
    registry = component_registry(value)
    activities = {binding["entityId"]: binding for binding in registry.values() if binding["kind"] == "ActivityCard"}
    changed_regions = set()
    for path in changed_paths:
        tokens = decode_pointer(path)
        if len(tokens) != 4 or tokens[:2] != ("entities", "activities") or tokens[2] not in activities or tokens[3] not in EDITABLE_FIELDS:
            deny("PATCH_OUT_OF_SCOPE", "投影仅允许已注册景点的授权叶子字段。", 403)
        binding = activities[tokens[2]]
        stop = next(item for item in value["tripState"]["days"][binding["dayKey"]]["stops"] if item["id"] == binding["stopId"])
        old_value = stop.get(tokens[3])
        stop[tokens[3]] = draft["entities"]["activities"][tokens[2]][tokens[3]]
        if tokens[3] == "startTime":
            value["editorMetadata"]["activityPeriods"][tokens[2]] = draft["entities"]["activities"][tokens[2]]["period"]
        if tokens[3] in {"startTime", "placeId"} and old_value != stop[tokens[3]]:
            changed_regions.add(binding["regionId"])
    payload = {
        "tripState": value["tripState"], "editorMetadata": value["editorMetadata"],
        "persistedConstraints": value["persistedConstraints"],
    }
    if changed_regions:
        routes = deepcopy(value.get("routeData", {}))
        for region in changed_regions:
            routes.pop(region, None)
        payload["routeData"] = routes
    return payload


def _business_projection(session: dict) -> dict:
    """Keep every legacy business field while indexing stops by persisted ID.

    TripDocument is a deliberately small compatibility projection. Checking the
    full original business payload too prevents an adapter defect from silently
    modifying e.g. a legacy day note/hotel field absent from that projection.
    """
    trip = deepcopy(session["tripState"])
    for day in trip["days"].values():
        stops = day.pop("stops")
        day["stops"] = {
            "order": [stop["id"] for stop in stops],
            "byId": {stop["id"]: stop for stop in stops},
        }
    return trip


def _assert_business_diff(before: dict, after: dict, changed_paths: list[str]) -> list[str]:
    registry = component_registry(before)
    bindings = {item["entityId"]: item for item in registry.values() if item["kind"] == "ActivityCard"}
    permitted = set()
    for path in changed_paths:
        tokens = decode_pointer(path)
        binding = bindings[tokens[2]]
        permitted.add(("days", binding["dayKey"], "stops", "byId", binding["stopId"], tokens[3]))
    changes = leaf_diff(_business_projection(before), _business_projection(after))
    if any(decode_pointer(path) not in permitted for path in changes):
        deny("PATCH_OUT_OF_SCOPE", "原业务状态的真实差异包含未授权字段。", 403)
    expected_metadata = deepcopy(before["editorMetadata"])
    for path in changed_paths:
        tokens = decode_pointer(path)
        if tokens[-1] == "startTime":
            binding = bindings[tokens[2]]
            stop = next(item for item in after["tripState"]["days"][binding["dayKey"]]["stops"] if item["id"] == binding["stopId"])
            expected_metadata["activityPeriods"][tokens[2]] = period_for_time(stop["startTime"])
    if expected_metadata != after["editorMetadata"] or before["persistedConstraints"] != after["persistedConstraints"]:
        deny("PATCH_OUT_OF_SCOPE", "业务补丁只能由服务端更新获准时间的派生时段，不能更改其他身份元数据或持久约束。", 403)
    return changes


def validate_patch(session: dict, selected_component_ids: list[str], patch: list[dict], fields: list[str] | None = None) -> dict:
    value = normalize_editor_state(session)
    scope = resolve_scope(value, selected_component_ids, fields)
    if not isinstance(patch, list) or not 1 <= len(patch) <= MAX_PATCH_OPERATIONS:
        deny("INVALID_REQUEST", "补丁应包含 1–16 个 replace 操作。")
    _assert_json(patch)
    try:
        size = len(json.dumps(patch, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        deny("INVALID_REQUEST", "补丁不是有效 JSON。")
    if size > MAX_PATCH_BYTES:
        deny("INVALID_REQUEST", "补丁超出 16KB 大小限制。", 413)
    current = to_trip_document(value)
    draft = deepcopy(current)
    authorised = {decode_pointer(path) for path in scope["allowedPaths"]}
    paths = set()
    catalog = {place["id"]: place for place in session_catalog(value)["places"]}
    for operation in patch:
        if not isinstance(operation, dict) or set(operation) != {"op", "path", "value"} or operation.get("op") != "replace":
            deny("INVALID_REQUEST", "仅允许具有 op/path/value 的 replace；不允许 add/remove/move/copy/test。")
        tokens = decode_pointer(operation["path"])
        if tokens in paths:
            deny("INVALID_REQUEST", "同一补丁不能重复写入同一字段。")
        paths.add(tokens)
        if scope["selectedLocked"]:
            deny("LOCKED_COMPONENT", "当前选中组件或其父级已锁定。", 423)
        # Exact decoded tuples: no parents, prefix collisions, implicit subtree grants.
        if tokens not in authorised:
            # A locked descendant is distinguished from an unrelated forbidden field.
            registry = component_registry(value)
            for cid, binding in registry.items():
                if binding["kind"] == "ActivityCard" and tuple(tokens[:3]) == ("entities", "activities", binding["entityId"]) and component_is_locked(cid, registry, scope["lockedComponentIds"]):
                    deny("LOCKED_COMPONENT", "目标景点或所属区域已锁定。", 423)
            deny("PATCH_OUT_OF_SCOPE", "补丁超出服务端允许的叶子字段范围。", 403)
        _get(draft, tokens)
        if not isinstance(operation["value"], str):
            deny("SCHEMA_INVALID", "景点描述、时间与地点ID必须是字符串。")
        if tokens[-1] == "description" and len(operation["value"]) > 2000:
            deny("SCHEMA_INVALID", "描述最多 2000 字符。")
        if tokens[-1] == "startTime":
            period_for_time(operation["value"])
        if tokens[-1] == "placeId" and operation["value"] not in catalog:
            deny("SCHEMA_INVALID", "请选择当前行程真实地点目录中的景点，不能使用虚构地点ID。")
        parent = _get(draft, tokens[:-1])
        parent[tokens[-1]] = operation["value"]
    direct_changes = leaf_diff(current, draft)
    # Derived presentation is server-owned. The Agent never writes title,
    # period, registry parent links or identity metadata itself.
    registry = component_registry(value)
    bindings = {item["entityId"]: item for item in registry.values() if item["kind"] == "ActivityCard"}
    for path in (operation["path"] for operation in patch):
        _, _, aid, field = decode_pointer(path)
        item = draft["entities"]["activities"][aid]
        if field == "startTime":
            period = period_for_time(item["startTime"])
            item["period"] = period
            draft["ui"]["components"]["card-" + aid]["parentId"] = "period-" + item["dayId"] + "-" + period
        elif field == "placeId":
            item["title"] = catalog[item["placeId"]]["name"]
            binding = bindings[aid]
            stop = next(stop for stop in value["tripState"]["days"][binding["dayKey"]]["stops"] if stop["id"] == binding["stopId"])
            if "description" not in stop and ("entities", "activities", aid, "description") not in paths:
                item["description"] = catalog[item["placeId"]].get("description", "")
    for day in draft["entities"]["days"].values():
        if any(path.endswith("/placeId") and decode_pointer(path)[2] in day["activityIds"] for path in direct_changes):
            places = [draft["entities"]["activities"][aid]["placeId"] for aid in day["activityIds"]]
            if len(places) != len(set(places)):
                deny("INVARIANT_VIOLATION", "同一天不能重复安排同一个真实景点，请选择其他备选。")
    validate_document_schema(draft)
    validate_invariants(draft, value["persistedConstraints"])
    changes = leaf_diff(current, draft)
    if not changes:
        deny("NO_CHANGE", "补丁没有实际改变授权字段。")
    derived = [path for path in changes if path not in direct_changes]
    if any(decode_pointer(path) not in authorised for path in direct_changes):
        deny("PATCH_OUT_OF_SCOPE", "真实状态差异包含未授权字段。", 403)
    for field in ("schemaVersion", "tripId", "version", "dayOrder"):
        if current[field] != draft[field]:
            deny("PATCH_OUT_OF_SCOPE", "内部元数据不能由业务补丁更改。", 403)
    projection_paths = [encode_pointer(decode_pointer(op["path"])) for op in patch]
    payload = project_back(value, draft, projection_paths)
    after = {**value, **deepcopy(payload)}
    validate_locked_unchanged(value, after)
    legacy_changes = _assert_business_diff(value, after, projection_paths)
    # This equality additionally ensures the adapter cannot introduce hidden changes.
    if to_trip_document(after) != draft:
        deny("INVARIANT_VIOLATION", "补丁投影与结构化状态不一致。")
    return {
        "document": current, "draft": draft, "changedPaths": changes, "derivedChangedPaths": derived,
        "effectiveScope": scope, "payload": payload, "legacyChangedPaths": legacy_changes,
        "validationReport": {"schema": True, "scope": True, "locks": True, "invariants": True, "leafDiff": True, "legacyBusinessDiff": True},
    }
