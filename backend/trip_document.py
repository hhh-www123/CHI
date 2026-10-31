"""Stable, read-only TripDocument projection over the existing itinerary storage.

The legacy tripState remains the single business source of truth. UUID5 identities
depend on persisted source IDs, never a current array position. Editor membership
and constraints are server metadata; a Patch can only alter explicitly mapped leaves.
"""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import UUID, uuid5

from fastapi import HTTPException
from jsonschema import Draft202012Validator

from .domain import day_label, region_map, session_catalog

_ID_NAMESPACE = UUID("05426a2a-1a9c-5d7c-9010-68f60b6a6ca1")
PERIODS = {"am": "上午", "pm": "下午", "evening": "晚上"}
TIME_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
_COMPONENT_KINDS = ["DayCard", "PeriodGroup", "ActivityCard"]


def deny(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def _identity(session: dict, kind: str, *parts: str) -> str:
    import json
    key = json.dumps([session.get("id", "legacy-unsaved"), kind, *parts], ensure_ascii=False)
    return kind + "-" + uuid5(_ID_NAMESPACE, key).hex


def day_id(session: dict, key: str) -> str:
    return _identity(session, "day", key)


def activity_id(session: dict, key: str, stop_id: str) -> str:
    return _identity(session, "activity", key, stop_id)


def period_for_time(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(TIME_PATTERN, value):
        deny("SCHEMA_INVALID", "开始时间必须是有效的 HH:mm。")
    return "am" if value < "12:00" else "pm" if value < "18:00" else "evening"


def normalize_editor_state(session: dict) -> dict:
    """Clone and seed frozen period membership, without a database write.

    The caller persists this metadata alongside the next authorised versioned
    mutation. Initial/earliest snapshots are preferred over later changed times.
    Payload-only undo snapshots have no trip id and must not invent identities.
    """
    result = deepcopy(session)
    metadata = result.setdefault("editorMetadata", {"schemaVersion": "1.0", "activityPeriods": {}})
    if not isinstance(metadata, dict) or metadata.get("schemaVersion") != "1.0" or not isinstance(metadata.get("activityPeriods"), dict):
        deny("SCHEMA_INVALID", "服务端组件身份元数据无效。")
    constraints = result.setdefault("persistedConstraints", {
        "lockedComponentIds": [],
        "hardConstraints": [{"id": "time-order", "kind": "time_order", "active": True}],
    })
    if not isinstance(constraints, dict) or not isinstance(constraints.get("lockedComponentIds"), list) or not isinstance(constraints.get("hardConstraints"), list):
        deny("SCHEMA_INVALID", "服务端持久约束无效。")
    if any(not isinstance(item, str) for item in constraints["lockedComponentIds"]):
        deny("SCHEMA_INVALID", "组件锁身份必须是字符串。")
    if not result.get("id"):
        return result
    references = [result.get("_initialPayload", {})] + result.get("_undo", []) + [result]
    initial_times = {}
    for reference in references:
        for key, day in reference.get("tripState", {}).get("days", {}).items():
            for stop in day.get("stops", []):
                initial_times.setdefault((key, stop.get("id")), stop.get("startTime"))
    for key, day in result.get("tripState", {}).get("days", {}).items():
        seen = set()
        for stop in day.get("stops", []):
            source_id = stop.get("id")
            if not isinstance(source_id, str) or not source_id or source_id in seen:
                deny("SCHEMA_INVALID", "景点卡片必须具有互不重复的持久 ID。")
            seen.add(source_id)
            aid = activity_id(result, key, source_id)
            if aid not in metadata["activityPeriods"]:
                metadata["activityPeriods"][aid] = period_for_time(initial_times[(key, source_id)])
            if metadata["activityPeriods"][aid] not in PERIODS:
                deny("SCHEMA_INVALID", "服务端时段映射无效。")
    return result


def _projection(session: dict) -> tuple[dict, dict]:
    value = normalize_editor_state(session)
    trip = value["tripState"]
    catalog = {place["id"]: place for place in session_catalog(value)["places"]}
    document = {
        "schemaVersion": "1.0", "tripId": value.get("id", "legacy-unsaved"),
        "version": value.get("version", 1), "title": trip["city"] + " · 城市旅行",
        "budgetLimit": trip["budgetLimit"], "dayOrder": [],
        "entities": {"days": {}, "activities": {}},
        "ui": {"componentCatalogVersion": "1.0", "components": {}},
    }
    registry = {}
    for region, key in region_map(value).items():
        day = trip["days"][key]
        did = day_id(value, key)
        cid = "card-" + did
        number = int(key[3:])
        label = day_label(number)
        activity_ids = [activity_id(value, key, stop["id"]) for stop in day["stops"]]
        document["dayOrder"].append(did)
        document["entities"]["days"][did] = {
            "id": did, "label": label, "date": day["date"], "activityIds": activity_ids,
        }
        registry[cid] = {
            "componentId": cid, "kind": "DayCard", "entityId": did,
            "label": label, "regionId": region, "dayKey": key, "editablePaths": [],
        }
        for period, title in PERIODS.items():
            pid = "period-" + did + "-" + period
            registry[pid] = {
                "componentId": pid, "kind": "PeriodGroup", "entityId": did,
                "label": label + title, "parentId": cid, "regionId": region,
                "dayKey": key, "period": period, "editablePaths": [],
            }
        for stop in day["stops"]:
            aid = activity_id(value, key, stop["id"])
            if stop["placeId"] not in catalog:
                deny("SCHEMA_INVALID", "行程引用的地点不在该会话目录中。")
            place = catalog[stop["placeId"]]
            period = value["editorMetadata"]["activityPeriods"][aid]
            paths = ["/entities/activities/" + aid + "/" + field for field in ("description", "startTime", "placeId")]
            document["entities"]["activities"][aid] = {
                "id": aid, "dayId": did, "period": period, "title": place["name"],
                "placeId": stop["placeId"], "startTime": stop["startTime"],
                "description": stop.get("description", place.get("description", "")),
            }
            activity_cid = "card-" + aid
            registry[activity_cid] = {
                "componentId": activity_cid, "kind": "ActivityCard", "entityId": aid,
                "label": place["name"], "parentId": "period-" + did + "-" + period,
                "regionId": region, "dayKey": key, "stopId": stop["id"],
                "period": period, "editablePaths": paths,
            }
            registry[cid]["editablePaths"].extend(paths)
            registry["period-" + did + "-" + period]["editablePaths"].extend(paths)
    for cid, binding in registry.items():
        binding["editablePaths"] = sorted(binding["editablePaths"])
        component = {"id": cid, "kind": binding["kind"], "entityId": binding["entityId"], "props": {}}
        if binding.get("parentId"):
            component["parentId"] = binding["parentId"]
        document["ui"]["components"][cid] = component
    validate_document_schema(document)
    return document, registry


def to_trip_document(session: dict) -> dict:
    return _projection(session)[0]


def component_registry(session: dict) -> dict:
    return _projection(session)[1]


def locked_component_ids(session: dict, registry: dict | None = None, include_legacy: bool = True) -> list[str]:
    value = normalize_editor_state(session)
    registry = registry if registry is not None else component_registry(value)
    result = set(value["persistedConstraints"]["lockedComponentIds"])
    legacy = set(value.get("controlState", {}).get("lockedRegions", []))
    result.update(cid for cid, binding in registry.items()
                  if include_legacy and binding["kind"] == "DayCard" and binding["regionId"] in legacy)
    for constraint in value["persistedConstraints"]["hardConstraints"]:
        if isinstance(constraint, dict) and constraint.get("active") is True and constraint.get("kind") == "immutable_subtree":
            target = constraint.get("targetId")
            if not isinstance(target, str) or target not in registry:
                deny("SCHEMA_INVALID", "不可变区域约束未指向注册组件。")
            result.add(target)
    if result - registry.keys():
        deny("SCHEMA_INVALID", "持久组件锁引用了未知组件。")
    return sorted(result)


def component_is_locked(component_id: str, registry: dict, locked: list[str]) -> bool:
    current = component_id
    visited = set()
    while current:
        if current in locked:
            return True
        if current in visited or current not in registry:
            deny("SCHEMA_INVALID", "组件注册表父子关系无效。")
        visited.add(current)
        current = registry[current].get("parentId")
    return False


def set_component_lock(session: dict, component_id: str, locked: bool) -> dict:
    value = normalize_editor_state(session)
    registry = component_registry(value)
    if component_id not in registry or type(locked) is not bool:
        deny("INVALID_REQUEST", "请选择已注册组件并明确锁定或解锁。")
    ids = set(value["persistedConstraints"]["lockedComponentIds"])
    if locked:
        ids.add(component_id)
    else:
        ids.discard(component_id)
    value["persistedConstraints"]["lockedComponentIds"] = sorted(ids)
    return {
        "editorMetadata": value["editorMetadata"],
        "persistedConstraints": value["persistedConstraints"],
        "controlState": value["controlState"],
    }


def _protected_values(session: dict, binding: dict) -> dict:
    value = normalize_editor_state(session)
    day = value.get("tripState", {}).get("days", {}).get(binding["dayKey"])
    if day is None:
        return {"missing": True}
    ui = value.get("uiSpec", {}).get("regions", {}).get(binding["regionId"])
    if binding["kind"] == "DayCard":
        return {"day": day, "ui": ui}
    if binding["kind"] == "ActivityCard":
        stop = next((item for item in day["stops"] if item["id"] == binding["stopId"]), None)
        return {"stop": stop, "ui": ui}
    members = [
        stop for stop in day["stops"]
        if value["editorMetadata"]["activityPeriods"].get(activity_id(value, binding["dayKey"], stop["id"])) == binding["period"]
    ]
    return {"stops": members, "ui": ui}


def validate_locked_unchanged(before_session: dict, after_session: dict) -> None:
    """Protect component locks on all mutation routes, including legacy APIs.

    This is deliberately conservative about presentation: a locked descendant
    also freezes its day's shared RegionUI. Locks/undo/reset are explicit control
    operations and the application decides when to invoke this guard.
    """
    before = normalize_editor_state(before_session)
    after = normalize_editor_state(after_session)
    registry = component_registry(before)
    if before["persistedConstraints"] != after["persistedConstraints"]:
        deny("LOCKED_COMPONENT", "业务更新不能更改服务端持久约束。", 423)
    for cid in locked_component_ids(before, registry, include_legacy=False):
        binding = registry[cid]
        if _protected_values(before, binding) != _protected_values(after, binding):
            deny("LOCKED_COMPONENT", "锁定范围‘" + binding["label"] + "’或其共享展示发生修改，正式状态保持不变。", 423)


_ACTIVITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "dayId", "period", "title", "placeId", "startTime", "description"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "dayId": {"type": "string", "minLength": 1},
        "period": {"enum": list(PERIODS)},
        "title": {"type": "string", "minLength": 1},
        "placeId": {"type": "string", "minLength": 1},
        "startTime": {"type": "string", "pattern": TIME_PATTERN},
        "description": {"type": "string", "maxLength": 2000},
    },
}
_DAY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["id", "label", "date", "activityIds"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "label": {"type": "string", "minLength": 1},
        "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "activityIds": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}},
    },
}
_COMPONENT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["id", "kind", "entityId", "props"],
    "properties": {
        "id": {"type": "string"}, "kind": {"enum": _COMPONENT_KINDS},
        "entityId": {"type": "string"}, "parentId": {"type": "string"},
        "props": {"type": "object", "maxProperties": 0},
    },
}
TRIP_DOCUMENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["schemaVersion", "tripId", "version", "title", "budgetLimit", "dayOrder", "entities", "ui"],
    "properties": {
        "schemaVersion": {"const": "1.0"}, "tripId": {"type": "string", "minLength": 1},
        "version": {"type": "integer", "minimum": 1},
        "title": {"type": "string", "minLength": 1},
        "budgetLimit": {"type": "number", "minimum": 0},
        "dayOrder": {"type": "array", "minItems": 1, "maxItems": 14, "uniqueItems": True, "items": {"type": "string"}},
        "entities": {
            "type": "object", "additionalProperties": False, "required": ["days", "activities"],
            "properties": {
                "days": {"type": "object", "additionalProperties": _DAY_SCHEMA},
                "activities": {"type": "object", "additionalProperties": _ACTIVITY_SCHEMA},
            },
        },
        "ui": {
            "type": "object", "additionalProperties": False, "required": ["componentCatalogVersion", "components"],
            "properties": {
                "componentCatalogVersion": {"const": "1.0"},
                "components": {"type": "object", "additionalProperties": _COMPONENT_SCHEMA},
            },
        },
    },
}
_DOCUMENT_VALIDATOR = Draft202012Validator(TRIP_DOCUMENT_SCHEMA)


def validate_document_schema(document: dict) -> None:
    errors = list(_DOCUMENT_VALIDATOR.iter_errors(document))
    if errors:
        deny("SCHEMA_INVALID", "结构化行程未通过 Schema 校验。")
    if set(document["dayOrder"]) != set(document["entities"]["days"]):
        deny("SCHEMA_INVALID", "日期顺序与日期实体不一致。")
    assigned = []
    for did in document["dayOrder"]:
        day = document["entities"]["days"][did]
        if day["id"] != did:
            deny("SCHEMA_INVALID", "日期实体 ID 与记录键不一致。")
        for aid in day["activityIds"]:
            activity = document["entities"]["activities"].get(aid)
            if activity is None or activity["id"] != aid or activity["dayId"] != did:
                deny("SCHEMA_INVALID", "景点实体与日期绑定不一致。")
            assigned.append(aid)
    if len(assigned) != len(set(assigned)) or set(assigned) != set(document["entities"]["activities"]):
        deny("SCHEMA_INVALID", "景点实体必须恰好属于一个日期。")
    components = document["ui"]["components"]
    for cid, component in components.items():
        if component["id"] != cid or component.get("parentId") == cid:
            deny("SCHEMA_INVALID", "组件稳定身份或父级绑定不一致。")
        if component.get("parentId") and component["parentId"] not in components:
            deny("SCHEMA_INVALID", "组件父级不存在。")
