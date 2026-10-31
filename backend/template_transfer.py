"""Copy presentation across dates without copying their itinerary or saved state."""
from __future__ import annotations

from copy import deepcopy
import re
from fastapi import HTTPException

from .domain import region_map, session_catalog

DISPLAY_FIELDS = ("view", "showCost", "showAlternatives", "compact", "showTransport", "showMap", "mapPlacement", "transportStyle")
DISPLAY_DEFAULTS = {"view": "cards", "showCost": True, "showAlternatives": False, "compact": False, "showTransport": False, "showMap": False, "mapPlacement": "afterStops", "transportStyle": "inline"}
_NUMBERS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二", 13: "十三", 14: "十四"}
_ENGLISH = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14}
_NUMBER = r"(?:\d{1,2}|[一二三四五六七八九十两]{1,3})"
_MARKER = r"@day-\d{1,2}@"
_STYLE = r"(?:页面|界面|样式|布局|风格|版式|模板|\blayout\b|\bstyle\b|\bappearance\b|\binterface\b|\bUI\b)"
_TRANSFER = r"(?:应用|套用|复用|同步|复制|沿用|照搬|采用|参照|参考|相同|一样|\bapply\b|\bcopy\b|\breuse\b|\buse\b|\bmatch\b|\bsame\b)"


def day_name(region: str) -> str:
    number = int(region.removeprefix("day-"))
    return "第" + _NUMBERS.get(number, str(number)) + "天"


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "两":
        return 2
    return next((number for number, text in _NUMBERS.items() if text == value), 0)


def canonical_dates(text: str, snapshot: dict | None = None) -> tuple[set[str], str]:
    """Resolve only explicit date references against this trip's actual dates."""
    regions = region_map(snapshot)
    mentioned: set[str] = set()

    def marker(number: int) -> str:
        region = f"day-{number}"
        if region not in regions:
            raise HTTPException(400, f"当前行程没有{day_name(region) if number else '第0天'}，请指定已有的行程日期。")
        mentioned.add(region)
        return f"@{region}@"

    def day_range(match):
        first, last = _number(match[1]), _number(match[2])
        if first > last:
            raise HTTPException(400, "行程日期范围需要按先后顺序填写。")
        return "、".join(marker(number) for number in range(first, last + 1))

    canonical = re.sub(rf"第\s*({_NUMBER})\s*(?:天|日)?\s*(?:到|至|~|—|-)\s*第?\s*({_NUMBER})\s*(?:天|日)", day_range, text)
    # Chinese shared suffix, e.g. 第二、三天, keeps both destinations explicit.
    def day_list(match):
        return "、".join(marker(_number(item)) for item in re.findall(_NUMBER, match[0]))
    canonical = re.sub(rf"第\s*{_NUMBER}(?:\s*(?:、|和|及|与|,)\s*第?\s*{_NUMBER})+\s*(?:天|日)", day_list, canonical)
    canonical = re.sub(rf"第\s*({_NUMBER})\s*(?:天|日)", lambda match: marker(_number(match[1])), canonical)
    canonical = re.sub(r"(?<![@\w])day\s*[- ]?\s*(\d{1,2})\b", lambda match: marker(int(match[1])), canonical, flags=re.I)
    english = "|".join(sorted(_ENGLISH, key=len, reverse=True))
    canonical = re.sub(rf"\b({english})\s+day\b", lambda match: marker(_ENGLISH[match[1].lower()]), canonical, flags=re.I)
    canonical = re.sub(r"\bday\s+(one|two|three)\b", lambda match: marker({"one": 1, "two": 2, "three": 3}[match[1].lower()]), canonical, flags=re.I)
    canonical = re.sub(r"首日", lambda _: marker(1), canonical)
    last = int(next(reversed(regions)).removeprefix("day-"))
    canonical = re.sub(r"最后(?:一)?(?:天|日)|返程当天|\blast\s+day\b", lambda _: marker(last), canonical, flags=re.I)
    if snapshot:
        for region, key in regions.items():
            date = snapshot["tripState"]["days"][key].get("date", "")
            if date and date in canonical:
                mentioned.add(region)
                canonical = canonical.replace(date, f"@{region}@")
    return mentioned, canonical


def is_negated_style_transfer(clause: str) -> bool:
    """A negated copy request never authorizes copying its mentioned dates."""
    if not re.search(_STYLE, clause, re.I) or not re.search(_TRANSFER, clause, re.I):
        return False
    return bool(re.search(r"(?:不要|别|请勿|不想|不用|不需要|无需|不能|do\s+not|don't|dont|never).{0,50}(?:应用|套用|复用|同步|复制|沿用|照搬|采用|copy|apply|reuse|use)", clause, re.I))


def parse_style_transfer(instruction: str, snapshot: dict, *, pure_only: bool = True) -> dict | None:
    """Recognize explicit source/target references, shortcutting only pure copies.

    Scope resolution also asks for references from mixed requests so the source
    remains read-only unless another clause explicitly asks to edit that day.
    """
    if not re.search(_STYLE, instruction, re.I) or not re.search(_TRANSFER, instruction, re.I):
        return None
    regions = region_map(snapshot)
    _, canonical = canonical_dates(instruction, snapshot)
    clauses = re.split(r"[，。；,;\n]|(?:但是|不过|并且|然后|另外|并)|\b(?:but|and\s+then)\b", canonical, flags=re.I)
    patterns = [
        rf"(?P<source>{_MARKER})[^@]{{0,120}}?{_STYLE}[^@]{{0,120}}?(?:应用|套用|复用|同步|复制|沿用|照搬)(?:到|于|至|给)?(?P<targets>.+)",
        rf"(?:apply|copy|reuse|use)\s*(?P<source>{_MARKER})[^@]{{0,120}}?{_STYLE}.{{0,50}}?\b(?:to|on|for)\b(?P<targets>.+)",
        rf"(?P<targets>{_MARKER}.{{0,180}}?)(?:采用|沿用|套用|参照|参考|应用|复用|使用|改成|与|跟)(?P<middle>[^@]{{0,40}})(?P<source>{_MARKER})[^@]{{0,100}}?(?:{_STYLE}|相同|一样)",
    ]
    matched = None
    for index, clause in enumerate(clauses):
        if is_negated_style_transfer(clause):
            continue
        match = next((found for pattern in patterns if (found := re.search(pattern, clause, re.I))), None)
        if match:
            matched = (index, clause, match)
            break
    if matched is None:
        return None
    index, transfer_clause, match = matched
    source = match['source'].strip('@')
    destinations = match['targets']
    consumed = {index}
    # A comma can separate target dates, but a later editing/protection clause
    # is not another destination merely because it mentions a date.
    for next_index in range(index + 1, len(clauses)):
        clause = clauses[next_index].strip()
        if re.fullmatch(rf"(?:{_MARKER})(?:\s*(?:和|及|与|以及|、|and|&)\s*{_MARKER})*", clause, re.I):
            destinations += '、' + clause
            consumed.add(next_index)
        else:
            break
    if pure_only:
        extra_action = r"添加|增加|新增|删除|删掉|移除|更换|替换|显示|隐藏|设为|设置|调整|优化|改为|改到|\badd\b|\bremove\b|\bhide\b|\bchange\b|\bupdate\b"
        protection = r"不要|不改|保留|保持|不变|别动|unchanged|preserve|\bkeep\b|do not|don't"
        # Compound instructions need normal Agent planning, including requests
        # to alter another UI field after copying the template.
        for clause_index, clause in enumerate(clauses):
            if not clause.strip() or re.search(protection, clause, re.I):
                continue
            if clause_index not in consumed:
                return None
            if re.search(extra_action, clause, re.I) or re.search(r"改成.{0,10}(?:时间轴|卡片|深色|浅色|颜色|布局)", clause):
                return None
        if any(is_negated_style_transfer(clause) for clause in clauses):
            return None
    targets = {item.strip('@') for item in re.findall(_MARKER, destinations)}
    if re.search(r"其余|其他(?:日期|天|页面|界面)|所有(?:日期|天|页面|界面)|全部(?:日期|天|页面|界面)|每天|每一天|各天|every day|all (?:other )?days|remaining days", destinations, re.I):
        targets.update(regions)
    targets.discard(source)
    from .scope import _whole_day_protection
    for clause in re.split(r"[，。；,;\n]|(?:但是|不过|但|并且|并)|\bbut\b", instruction, flags=re.I):
        mentioned, protected_text = canonical_dates(clause, snapshot)
        if _whole_day_protection(protected_text):
            targets.difference_update(mentioned)
    if not targets:
        return None
    return {'sourceRegionId': source, 'targetRegionIds': [region for region in regions if region in targets]}


def _regions_for_transfer(snapshot: dict, source: str, targets: list[str]) -> tuple[dict[str, str], list[str]]:
    regions = region_map(snapshot)
    if source not in regions:
        raise HTTPException(404, "没有找到要复用的源日期。")
    if not targets or any(target not in regions for target in targets):
        raise HTTPException(400, "请选择已有的目标行程日期。")
    selected = [region for region in regions if region in targets and region != source]
    if not selected:
        raise HTTPException(400, "请选择源日期之外的目标日期。")
    for region in selected:
        if region in snapshot.get("controlState", {}).get("lockedRegions", []):
            raise HTTPException(423, day_name(region) + "已锁定，不能应用页面样式。")
    return regions, selected


def transfer_scope(snapshot: dict, source: str, targets: list[str]):
    """Derive exact UI rights from the chosen source, rather than guessing fields."""
    from . import agent
    regions, selected = _regions_for_transfer(snapshot, source, targets)
    ui = snapshot["uiSpec"]["regions"][source]
    values = {field: deepcopy(ui.get(field, DISPLAY_DEFAULTS[field])) for field in DISPLAY_FIELDS}
    new_types = list(dict.fromkeys(block["type"] for block in ui.get("blocks", []) if block.get("type") in {"note", "checklist", "table"}))
    return agent.EditScope(message="复用源页面的展示方式，保留每个目标日期的行程与已有内容。", permissions=[agent.RegionPermission(
        regionId=region, uiFields=list(values), requiredUI=agent.UIValues(**values),
        editSurface=True, replacePresentation=bool(ui.get("surface") and ui["surface"].get("placement") == "replaceStops"),
        newBlockTypes=new_types,
    ) for region in selected])


def _surface_for_target(snapshot: dict, source: str, target: str) -> dict | None:
    regions = region_map(snapshot)
    original = snapshot["uiSpec"]["regions"][source].get("surface")
    if not original:
        return None
    surface = deepcopy(original)
    source_day = snapshot["tripState"]["days"][regions[source]]
    target_day = snapshot["tripState"]["days"][regions[target]]
    # The wrapper label is a presentation detail and can be rebound safely.
    for before, after in ((source_day.get("title", ""), target_day.get("title", "")), (day_name(source), day_name(target))):
        if before:
            surface["title"] = surface.get("title", "").replace(before, after)
    authored = "\n".join(str(surface.get(key, "")) for key in ("html", "javascript", "title"))
    css_content = re.findall(r"content\s*:\s*([\x27\x22])(.*?)\1", str(surface.get("css", "")), re.I | re.S)
    authored += "\n" + "\n".join(value for _, value in css_content)
    literals = {source_day.get("date", ""), source_day.get("title", ""), source_day.get("note", ""), day_name(source)}
    catalog = session_catalog(snapshot)
    place_by_id = {place["id"]: place for place in catalog["places"]}
    for index, stop in enumerate(source_day.get("stops", [])):
        target_stops = target_day.get("stops", [])
        if index >= len(target_stops) or stop.get("startTime") != target_stops[index].get("startTime"):
            literals.add(stop.get("startTime", ""))
        literals.update([stop.get("id", ""), stop.get("placeId", "")])
        place = place_by_id.get(stop.get("placeId"), {})
        literals.update(str(place.get(field, "")) for field in ("name", "address", "mapQuery"))
    if any(len(literal) >= 3 and literal in authored for literal in literals if literal):
        raise HTTPException(422, "源页面包含源日期专属的景点、日期或备注，需要Agent按目标日期重新绑定模板，不能直接复制行程内容。")
    key = regions[source]
    if re.search(rf"(?:days|trip\s*\.\s*days)\s*(?:\.\s*{re.escape(key)}\b|\[\s*['\"]{re.escape(key)}['\"]\s*\])|\bDAY\s+0?{source.removeprefix('day-')}\b", authored, re.I):
        raise HTTPException(422, "源页面固定读取了源日期，需要Agent将其改为当前日期的数据绑定。")
    from .generated_ui import GeneratedSurface, validate_surface
    checked = GeneratedSurface.model_validate(surface)
    validate_surface(checked)
    return checked.model_dump()


def copy_style_updates(snapshot: dict, source: str, targets: list[str]) -> list[dict]:
    """Return only nextUI; never copy source stops, routes, blocks or widget state."""
    from .scope import validate_updates
    regions, selected = _regions_for_transfer(snapshot, source, targets)
    source_ui = snapshot["uiSpec"]["regions"][source]
    if source_ui.get("blocks"):
        # Text blocks may contain source-only trip facts. Recreate their template
        # through the Agent while preserving every existing destination block.
        raise HTTPException(422, "源页面含补充内容块，需要Agent将其结构按目标日期适配，已有目标内容不能直接覆盖。")
    updates = []
    for target in selected:
        ui = deepcopy(snapshot["uiSpec"]["regions"][target])
        ui.update({field: deepcopy(source_ui.get(field, DISPLAY_DEFAULTS[field])) for field in DISPLAY_FIELDS})
        ui["id"] = target
        ui["binding"] = f"days.{regions[target]}"
        ui["surface"] = _surface_for_target(snapshot, source, target)
        updates.append({"regionId": target, "nextUI": ui})
    if all(update["nextUI"] == snapshot["uiSpec"]["regions"][update["regionId"]] for update in updates):
        raise HTTPException(400, "所选日期已经使用相同的页面样式，无需重复应用。")
    validate_updates(snapshot, transfer_scope(snapshot, source, selected), updates)
    return updates