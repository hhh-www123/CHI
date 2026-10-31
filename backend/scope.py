"""Editing contracts for generated interfaces and the separate travel data."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Iterable

from fastapi import HTTPException

from . import agent
from .domain import region_map
from .template_transfer import canonical_dates, day_name, is_negated_style_transfer, parse_style_transfer

REGIONS = {"day-1": "day1", "day-2": "day2", "day-3": "day3"}
UI_LABELS = {"view": "展示方式", "showCost": "费用显示", "showAlternatives": "室内备选", "compact": "信息密度", "showTransport": "景点间交通", "showMap": "行程地图", "transportMode": "交通偏好", "mapPlacement": "地图位置", "transportStyle": "交通排列"}
DAY_NAMES = {"day-1": "第一天", "day-2": "第二天", "day-3": "第三天"}


def _clause_dates(clause: str, snapshot: dict | None) -> tuple[set[str], str]:
    return canonical_dates(clause, snapshot)


_ALL_DAYS = r"每天|每一天|各天|所有(?:的)?(?:日期|日程|天)|全部(?:的)?(?:日期|日程|天)|整个行程|\bevery\s+day\b|\ball\s+(?:the\s+)?days\b|\bwhole\s+trip\b"
_PROTECTION = r"保持不变|保持原样|维持原样|维持不变|保留|不要(?:修改|更改|改动|改|调整|动)|不(?:修改|更改|改动|改|调整|动)|不变|别改|别动|unchanged|(?:do\s+not|don't|dont)\s+(?:change|modify|edit)|\bkeep\b|\bpreserve\b"
_EDIT_ACTION = r"添加|增加|新增|改成|改为|更换|替换|显示|隐藏|生成|切换|优化|调整|整理|补充|安排|放在|放到|应用|套用|复制|复用|同步|沿用|\badd\b|\bshow\b|\bhide\b|\bchange\b|\bupdate\b|\bmodify\b|\bedit\b"


def _whole_day_protection(canonical: str) -> bool:
    """Only a whole-date expression can exclude a day, never a named field.

    '@day-2@景点不变' keeps its field noun and cannot match these patterns.
    '@day-1@保持原样' can. Avoid wildcard distances across intervening nouns.
    """
    value = re.sub(_ALL_DAYS, "@all@", canonical, flags=re.I).lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"^(?:请|麻烦|please|把|将)", "", value)
    value = value.replace("the@", "@")
    dates = r"@(?:day-\d{1,2}|all)@(?:(?:和|及|与|以及|、|and|&|\+)@(?:day-\d{1,2}|all)@)*"
    whole = r"(?:的)?(?:都|整体|整天|所有内容|任何内容|什么都|should|must)?"
    suffix = r"(?:不改|不改动|不要改|不要改动|不要修改|不要更改|不要调整|别改|不修改|不更改|不调整|保留(?:原样|不变)?|保持不变|保持原样|维持原样|维持不变|不动|不要动|别动|不用改|不需要改|无需修改|不能修改|remainunchanged|unchanged)"
    prefix = r"(?:不要|别|勿|不必|无需|不用|不能)(?:再)?(?:修改|更改|改动|改|调整|动)|donotchange|donotmodify|donotedit|don'tchange|don'tmodify|don'tedit"
    tail = r"(?:任何内容|全部内容|任何东西)?"
    return bool(re.fullmatch(rf"(?:{dates}{whole}{suffix}{tail}|(?:{prefix}){dates}{tail}|(?:保留|维持|保持|keep|preserve){dates}(?:原样|不变|unchanged)?)", value))


def requested_regions(instruction: str, target: str, snapshot: dict | None = None) -> set[str]:
    """Resolve explicit edits and whole-day exceptions before trusting a model.

    A field-preserving date is context for a following unqualified edit, rather
    than permission to modify that field or a prohibition on the whole day.
    Whole-day prohibitions are subtracted last and can never be reauthorized by
    the dropdown or an overlapping 'all days' expression.
    """
    regions = region_map(snapshot)
    transfer = parse_style_transfer(instruction, snapshot, pure_only=False) if snapshot else None
    positive, contextual, excluded = set(), set(), set()
    all_days = False
    restricted = set()
    has_unqualified_edit = False
    prohibited_transfer = False
    last_context = set()
    for clause in re.split(r"[，。；,;\n]|(?:但是|不过|但|并且|并)|\bbut\b", instruction, flags=re.I):
        if not clause.strip():
            continue
        if is_negated_style_transfer(clause):
            prohibited_transfer = True
            continue
        mentioned, canonical = _clause_dates(clause, snapshot)
        if transfer and snapshot:
            local_transfer = parse_style_transfer(clause, snapshot, pure_only=False)
            if local_transfer and local_transfer['sourceRegionId'] == transfer['sourceRegionId']:
                mentioned.discard(transfer['sourceRegionId'])
        clause_all = bool(re.search(_ALL_DAYS, clause, re.I))
        if _whole_day_protection(canonical):
            excluded.update(regions if clause_all else mentioned)
            continue
        protected = bool(re.search(_PROTECTION, clause, re.I))
        remaining = re.sub(_PROTECTION, "", clause, flags=re.I)
        pure_field_protection = protected and not re.search(_EDIT_ACTION, remaining, re.I)
        if pure_field_protection:
            contextual.update(mentioned)
            if mentioned:
                last_context = mentioned
            continue
        if clause_all:
            all_days = True
        if mentioned:
            positive.update(mentioned)
            last_context = mentioned
            if re.search(r"只(?:修改|改|调整|对)|仅(?:修改|改|调整|对)|\bonly\b", clause, re.I):
                restricted.update(mentioned)
        elif not clause_all:
            has_unqualified_edit = True
            if last_context:
                positive.update(last_context)
    if transfer:
        requested = set(transfer["targetRegionIds"]) | positive
    elif restricted:
        requested = restricted
    elif all_days:
        requested = set(regions)
    elif positive:
        requested = positive
    elif contextual and has_unqualified_edit:
        requested = contextual
    elif contextual:
        requested = contextual
    elif prohibited_transfer:
        requested = set()
    else:
        requested = set(regions) if target == "all" else {target}
    result = requested - excluded
    if not result.issubset(regions):
        raise HTTPException(400, "请选择当前行程已有的日期。")
    return result


def freeze_scope(snapshot: dict, scope: agent.EditScope, instruction: str, target: str) -> agent.EditScope:
    regions = region_map(snapshot)
    if scope.clarification or scope.unsupportedCapabilities:
        raise HTTPException(400, scope.clarification or "当前尚未接入这些能力：" + "、".join(scope.unsupportedCapabilities))
    trip_fields = scope.tripFields
    if not scope.permissions and not trip_fields:
        raise HTTPException(400, "请说明需要修改的界面内容，当前没有获得任何修改权限。")
    allowed_regions = requested_regions(instruction, target, snapshot)
    if scope.permissions and not allowed_regions:
        raise HTTPException(400, "所有相关日期都被要求保留，当前没有可修改的日期。请明确允许修改的日期。")
    frozen = scope.model_copy(deep=True)
    frozen.tripFields = list(dict.fromkeys(frozen.tripFields))
    if snapshot["controlState"].get("hotelLocked") and {"hotelId", "nights"}.intersection(frozen.tripFields):
        raise HTTPException(423, "酒店选择已锁定，本次方案未作任何修改。请先解锁酒店。")
    seen = set()
    for permission in frozen.permissions:
        region = permission.regionId
        if region in seen or region not in regions or region not in allowed_regions:
            raise HTTPException(400, "范围识别涉及未获授权的日期，已阻止生成。请明确要修改的那一天。")
        seen.add(region)
        if region in snapshot["controlState"]["lockedRegions"]:
            raise HTTPException(423, f"{day_name(region)}已锁定，本次方案未作任何修改。请先解锁该天，或缩小修改范围。")
        if permission.replacePresentation and not permission.editSurface:
            raise HTTPException(400, "替换行程展示需要同时授权修改生成式界面。")
        day = snapshot["tripState"]["days"][regions[region]]
        ui = snapshot["uiSpec"]["regions"][region]
        stops = {stop["id"] for stop in day["stops"]}
        blocks = {block["id"] for block in ui.get("blocks", [])}
        if not set(permission.placeStopIds + permission.timeStopIds).issubset(stops):
            raise HTTPException(400, "范围识别引用了不存在的景点条目，已停止生成。")
        if not set(permission.editableBlockIds + permission.removableBlockIds).issubset(blocks):
            raise HTTPException(400, "范围识别引用了不存在的补充内容，已停止生成。")
        # A new map/transport can vary within its own component, without
        # authorizing cost, view, density, POIs, time or notes.
        if "showMap" in permission.uiFields and "mapPlacement" not in permission.uiFields:
            permission.uiFields.append("mapPlacement")
        if "showTransport" in permission.uiFields and "transportStyle" not in permission.uiFields:
            permission.uiFields.append("transportStyle")
        permission.uiFields = list(dict.fromkeys(permission.uiFields))
        required = permission.requiredUI.model_dump(exclude_none=True)
        if not set(required).issubset(permission.uiFields):
            raise HTTPException(400, "目标值与允许修改字段不一致，已停止生成。")
        has_permission = (permission.uiFields or permission.placeStopIds or permission.timeStopIds or permission.editNote
                          or permission.editableBlockIds or permission.removableBlockIds or permission.newBlockTypes
                          or permission.editSurface or permission.editStructure or permission.editTitle or permission.editDate)
        if not has_permission:
            raise HTTPException(400, f"{day_name(region)}没有明确的可修改内容，请补充说明。")
    # A global-only edit deliberately grants no day permissions. When day
    # permissions exist, a daily request must still cover every requested day.
    if seen and seen != allowed_regions:
        raise HTTPException(502, "范围识别未覆盖所请求的全部日期，行程未修改。请明确范围后重新生成。")
    return frozen


def permissions_by_region(scope: agent.EditScope) -> dict[str, agent.RegionPermission]:
    return {permission.regionId: permission for permission in scope.permissions}


def validate_trip_changes(snapshot: dict, scope: agent.EditScope, changes: dict | None) -> None:
    """Global fields stay separate from generated HTML and day-level rights."""
    if changes is None:
        return
    if not isinstance(changes, dict) or set(changes) - {"hotelId", "budgetLimit", "nights"}:
        raise HTTPException(400, "候选包含未知的全局行程修改。")
    before = snapshot["tripState"]
    for key, value in changes.items():
        if value == before.get(key):
            continue
        if key not in scope.tripFields:
            raise HTTPException(400, "候选修改了未授权的酒店、预算或住宿晚数。")
        if key in {"hotelId", "nights"} and snapshot["controlState"].get("hotelLocked"):
            raise HTTPException(423, "酒店选择已锁定，不能应用这项修改。")
        if key == "hotelId" and (not isinstance(value, str) or not value.strip()):
            raise HTTPException(400, "候选酒店标识无效。")
        if key == "budgetLimit" and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 100 <= value <= 100000):
            raise HTTPException(400, "预算请输入100至100000之间的数字。")
        if key == "nights" and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 30):
            raise HTTPException(400, "住宿晚数需要是0至30之间的整数。")


def _validate_surface(before: dict | None, after: dict | None, permission: agent.RegionPermission) -> None:
    if before == after:
        return
    if not permission.editSurface:
        raise HTTPException(400, "候选改变了范围外的生成式界面。")
    if (after and after.get("placement") == "replaceStops"
            and (not before or before.get("placement") != "replaceStops")
            and not permission.replacePresentation):
        raise HTTPException(400, "本次只允许增补交互组件，不能替换原有行程展示。")


def _validate_day(before: dict, after: dict, permission: agent.RegionPermission) -> None:
    if set(after) != set(before):
        raise HTTPException(400, "候选改变了行程数据字段结构。")
    for key in before:
        if key not in {"note", "stops", "title", "date"} and before[key] != after[key]:
            raise HTTPException(400, "候选修改了未知行程字段。")
    if before["title"] != after["title"] and not permission.editTitle:
        raise HTTPException(400, "候选修改了受保护的行程标题。")
    if before["date"] != after["date"] and not permission.editDate:
        raise HTTPException(400, "候选修改了受保护的出行日期。")
    if before["note"] != after["note"] and not permission.editNote:
        raise HTTPException(400, "候选改变了受保护的备注。")
    old_stops, new_stops = before["stops"], after["stops"]
    if not isinstance(new_stops, list) or len(new_stops) > 24:
        raise HTTPException(400, "候选景点条目数量无效。")
    identifiers = [stop.get("id") for stop in new_stops if isinstance(stop, dict)]
    if len(identifiers) != len(new_stops) or any(not isinstance(item, str) or not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
        raise HTTPException(400, "候选包含无效或重复的景点条目标识。")
    if not permission.editStructure and [stop["id"] for stop in old_stops] != identifiers:
        raise HTTPException(400, "候选增删或重排了未授权的景点条目。")
    old_by_id = {stop["id"]: stop for stop in old_stops}
    for second in new_stops:
        if set(second) != {"id", "placeId", "startTime"}:
            raise HTTPException(400, "候选修改了未知景点字段。")
        if not isinstance(second["placeId"], str) or not isinstance(second["startTime"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", second["startTime"]):
            raise HTTPException(400, "候选景点或开始时间格式无效。")
        first = old_by_id.get(second["id"])
        if first is None:
            if not permission.editStructure:
                raise HTTPException(400, "候选新增了未授权的景点。")
            continue
        if first["placeId"] != second["placeId"] and first["id"] not in permission.placeStopIds:
            raise HTTPException(400, "候选替换了用户未指定的景点。")
        if first["startTime"] != second["startTime"] and first["id"] not in permission.timeStopIds:
            raise HTTPException(400, "候选改变了受保护的时间。")
    if len({stop["placeId"] for stop in new_stops}) != len(new_stops):
        raise HTTPException(400, "候选在同一天重复安排了相同景点。")


def validate_operations(snapshot: dict, scope: agent.EditScope, operations: Iterable[agent.Operation]) -> None:
    regions = region_map(snapshot)
    permissions = permissions_by_region(scope)
    for operation in operations:
        permission = permissions.get(operation.regionId)
        if permission is None:
            raise HTTPException(400, "候选修改了范围外的日期。")
        old_ui = snapshot["uiSpec"]["regions"][operation.regionId]
        if isinstance(operation, agent.SetUI):
            fields = operation.model_dump(exclude_none=True, exclude={"kind", "regionId"})
            if not fields:
                raise HTTPException(400, "候选包含空的界面修改。")
            if any(key not in permission.uiFields and old_ui.get(key) != value for key, value in fields.items()):
                raise HTTPException(400, "候选修改了用户未要求的界面字段。")
        elif isinstance(operation, agent.SetSurface):
            if not permission.editSurface:
                raise HTTPException(400, "候选修改了范围外的生成式界面。")
            surface = operation.surface.model_dump() if operation.surface is not None else None
            _validate_surface(old_ui.get("surface"), surface, permission)
        elif isinstance(operation, agent.SetDay):
            before = snapshot["tripState"]["days"][regions[operation.regionId]]
            fields = operation.model_dump(exclude_none=True, exclude={"kind", "regionId"})
            if not fields:
                raise HTTPException(400, "候选包含空的日程修改。")
            _validate_day(before, {**deepcopy(before), **fields}, permission)
        elif isinstance(operation, agent.ReplaceStop):
            if operation.stopId not in permission.placeStopIds:
                raise HTTPException(400, "候选替换了范围外的景点条目。")
        elif isinstance(operation, agent.SetTime):
            if operation.stopId not in permission.timeStopIds:
                raise HTTPException(400, "候选修改了未授权的开始时间。")
        elif isinstance(operation, agent.SetNote):
            if not permission.editNote:
                raise HTTPException(400, "候选修改了受保护的行程备注。")
        elif isinstance(operation, agent.UpsertBlock):
            old_blocks = {block["id"]: block for block in old_ui.get("blocks", [])}
            if operation.block.id in old_blocks:
                if operation.block.id not in permission.editableBlockIds:
                    raise HTTPException(400, "候选覆盖了受保护的既有内容块。")
            elif operation.block.type not in permission.newBlockTypes:
                raise HTTPException(400, "候选添加了未获授权的内容类型。")
        elif isinstance(operation, agent.RemoveBlock):
            if operation.blockId not in permission.removableBlockIds:
                raise HTTPException(400, "候选删除了受保护的既有内容块。")
        else:
            raise HTTPException(400, "候选包含未知操作。")


def _validate_blocks(before: list[dict], after: list[dict], permission: agent.RegionPermission) -> None:
    old = {block["id"]: block for block in before}
    new = {block["id"]: block for block in after}
    if len(new) != len(after):
        raise HTTPException(400, "候选包含重复的内容块标识。")
    for identifier, block in old.items():
        if identifier not in new and identifier not in permission.removableBlockIds:
            raise HTTPException(400, "候选丢失了受保护的内容块。")
        if identifier in new and block != new[identifier] and identifier not in permission.editableBlockIds:
            raise HTTPException(400, "候选改变了范围外的既有内容。")
    for identifier, block in new.items():
        if identifier not in old and block.get("type") not in permission.newBlockTypes:
            raise HTTPException(400, "候选新增了未授权的内容。")
    if [block["id"] for block in after if block["id"] in old] != [block["id"] for block in before if block["id"] in new]:
        raise HTTPException(400, "候选重排了受保护的既有内容。")


def validate_updates(snapshot: dict, scope: agent.EditScope, updates: list[dict]) -> None:
    """Recheck final differences before map queries and immediately on apply."""
    regions = region_map(snapshot)
    permissions = permissions_by_region(scope)
    seen = set()
    for update in updates:
        region = update["regionId"]
        if region in seen or region not in permissions:
            raise HTTPException(400, "候选结果的日期范围不符合已确认的约束。")
        if set(update) - {"regionId", "nextUI", "nextData", "routeData"}:
            raise HTTPException(400, "候选结果包含未知修改。")
        seen.add(region)
        permission = permissions[region]
        old_ui = snapshot["uiSpec"]["regions"][region]
        ui = update["nextUI"]
        dynamic_fields = {"surface", "surfaceState"}
        if set(ui) - set(old_ui) - dynamic_fields or set(old_ui) - set(ui) - dynamic_fields:
            raise HTTPException(400, "候选改变了界面字段结构。")
        for key in set(old_ui) | set(ui):
            if key == "blocks":
                _validate_blocks(old_ui.get("blocks", []), ui.get("blocks", []), permission)
            elif key == "surface":
                _validate_surface(old_ui.get(key), ui.get(key), permission)
            elif key == "surfaceState":
                if old_ui.get(key, {}) != ui.get(key, {}):
                    raise HTTPException(400, "候选改变了用户已有的组件交互状态。")
            elif old_ui.get(key) != ui.get(key) and key not in permission.uiFields:
                raise HTTPException(400, "候选改变了范围外的界面设置。")
        for key, expected in permission.requiredUI.model_dump(exclude_none=True).items():
            if ui.get(key) != expected:
                raise HTTPException(400, "候选没有满足用户明确要求的展示目标。")
        before = snapshot["tripState"]["days"][regions[region]]
        day = update.get("nextData", before)
        _validate_day(before, day, permission)
        old_ids = {stop["id"] for stop in before["stops"]}
        new_ids = {stop["id"] for stop in day["stops"]} - old_ids
        other_ids = {stop["id"] for key, other_day in snapshot["tripState"]["days"].items() if key != regions[region] for stop in other_day["stops"]}
        if new_ids & other_ids:
            raise HTTPException(400, "候选复用了其他日期的稳定景点标识。")
        if "routeData" in update and update["routeData"] != snapshot.get("routeData", {}).get(region):
            route_permission = ({"showMap", "showTransport", "transportMode"}.intersection(permission.uiFields)
                                or permission.placeStopIds or permission.timeStopIds or permission.editStructure or permission.editDate)
            if not route_permission:
                raise HTTPException(400, "纯展示候选改变了范围外的既有路线数据。")
    for region, permission in permissions.items():
        if region not in seen:
            old_ui = snapshot["uiSpec"]["regions"][region]
            requirements = permission.requiredUI.model_dump(exclude_none=True)
            needs_update = (not requirements or any(old_ui.get(key) != value for key, value in requirements.items())
                            or permission.placeStopIds or permission.timeStopIds or permission.editNote
                            or permission.editableBlockIds or permission.removableBlockIds or permission.newBlockTypes
                            or permission.editSurface or permission.editStructure or permission.editTitle or permission.editDate)
            if needs_update:
                raise HTTPException(400, "候选没有覆盖所请求的全部日期内容。")


TRIP_LABELS = {"hotelId": "酒店选择", "budgetLimit": "总预算", "nights": "住宿晚数"}


def scope_summary(scope: agent.EditScope) -> list[str]:
    result = []
    for permission in scope.permissions:
        labels = [UI_LABELS[field] for field in permission.uiFields]
        if permission.editSurface:
            labels.append("Agent生成的界面与交互")
        if permission.replacePresentation:
            labels.append("重排行程展示")
        if permission.editStructure:
            labels.append("景点增删与顺序")
        if permission.editTitle:
            labels.append("本日标题")
        if permission.editDate:
            labels.append("本日出行日期")
        if permission.placeStopIds:
            labels.append(f"指定的{len(permission.placeStopIds)}个景点")
        if permission.timeStopIds:
            labels.append(f"指定的{len(permission.timeStopIds)}个开始时间")
        if permission.editNote:
            labels.append("本日备注")
        if permission.editableBlockIds or permission.removableBlockIds:
            labels.append("指定补充内容")
        if permission.newBlockTypes:
            labels.append("新增" + "、".join({"note": "说明", "checklist": "清单", "table": "表格"}[kind] for kind in permission.newBlockTypes))
        result.append(day_name(permission.regionId) + "：" + "、".join(labels))
    if scope.tripFields:
        result.append("全局行程：" + "、".join(TRIP_LABELS[field] for field in scope.tripFields))
    return result


def preserved_summary(scope: agent.EditScope, snapshot: dict | None = None) -> list[str]:
    regions = region_map(snapshot)
    permissions = scope.permissions
    global_protected = [label for field, label in TRIP_LABELS.items() if field not in scope.tripFields]
    global_protected.append("城市")
    if not any(item.editDate for item in permissions):
        global_protected.append("出行日期")
    protected = ["、".join(global_protected)]
    unmentioned = set(regions) - {permission.regionId for permission in permissions}
    if unmentioned:
        protected.append("、".join(day_name(region) for region in regions if region in unmentioned) + "的行程与界面")
    if any(item.editStructure for item in permissions):
        protected.append("未获授权的景点数据及其他日期景点")
    else:
        protected.append("未指定的景点" if any(item.placeStopIds for item in permissions) else "所有景点选择")
    protected.append("未指定的既有开始时间" if any(item.timeStopIds for item in permissions) else "既有景点的开始时间")
    protected.append("未授权的行程备注" if any(item.editNote for item in permissions) else "所有行程备注")
    protected.append("未指定的既有补充内容" if any(item.editableBlockIds or item.removableBlockIds for item in permissions) else "全部既有补充内容")
    protected.append("未涉及的界面设置与已有组件交互状态")
    return protected


def describe_changes(snapshot: dict, updates: list[dict], places: dict[str, dict]) -> list[str]:
    regions = region_map(snapshot)
    changes = []
    for update in updates:
        region = update["regionId"]
        label = day_name(region)
        before_ui = snapshot["uiSpec"]["regions"][region]
        after_ui = update["nextUI"]
        for field, field_name in UI_LABELS.items():
            if before_ui.get(field) != after_ui.get(field):
                value = after_ui.get(field)
                friendly = {"beforeStops": "行程上方", "afterStops": "行程下方", "inline": "逐段展示", "summary": "集中汇总", "cards": "卡片", "timeline": "时间轴"}.get(str(value), "显示" if value is True else "隐藏" if value is False else str(value))
                changes.append(f"{label}：{field_name}改为{friendly}")
        if before_ui.get("surface") != after_ui.get("surface"):
            surface = after_ui.get("surface")
            changes.append(f"{label}：{'更新Agent生成的界面“' + surface['title'] + '”' if surface else '移除Agent生成的界面'}")
        before_blocks = {block["id"]: block for block in before_ui.get("blocks", [])}
        after_blocks = {block["id"]: block for block in after_ui.get("blocks", [])}
        for identifier, block in after_blocks.items():
            if before_blocks.get(identifier) != block:
                changes.append(f"{label}：{'添加' if identifier not in before_blocks else '修改'}“{block['title']}”")
        for identifier, block in before_blocks.items():
            if identifier not in after_blocks:
                changes.append(f"{label}：移除“{block['title']}”")
        before = snapshot["tripState"]["days"][regions[region]]
        day = update.get("nextData", before)
        for field, field_name in (("title", "本日标题"), ("date", "出行日期"), ("note", "本日备注")):
            if before[field] != day[field]:
                changes.append(f"{label}：修改{field_name}" + (f"为{day[field]}" if field != "note" else ""))
        old_stops = {stop["id"]: stop for stop in before["stops"]}
        new_stops = {stop["id"]: stop for stop in day["stops"]}
        for identifier, first in old_stops.items():
            if identifier not in new_stops:
                changes.append(f"{label}：移除景点{places.get(first['placeId'], {}).get('name', first['placeId'])}")
        for identifier, second in new_stops.items():
            second_name = places.get(second["placeId"], {}).get("name", second["placeId"])
            first = old_stops.get(identifier)
            if first is None:
                changes.append(f"{label}：添加景点{second_name}（{second['startTime']}）")
                continue
            if first["placeId"] != second["placeId"]:
                changes.append(f"{label}：{places.get(first['placeId'], {}).get('name', first['placeId'])}改为{second_name}")
            if first["startTime"] != second["startTime"]:
                changes.append(f"{label}：{second_name}开始时间改为{second['startTime']}")
        shared = old_stops.keys() & new_stops.keys()
        if [stop["id"] for stop in before["stops"] if stop["id"] in shared] != [stop["id"] for stop in day["stops"] if stop["id"] in shared]:
            changes.append(f"{label}：调整既有景点顺序")
    return changes
