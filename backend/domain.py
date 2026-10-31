"""Session-local trip identifiers and verified catalog access."""
from __future__ import annotations

from copy import deepcopy
import re

from .catalog import CATALOG_VERSION, HOTELS, PLACES

MAX_DAYS = 14
REGION_PATTERN = r"^day-(?:[1-9]|1[0-4])$"
_DAY_KEY = re.compile(r"^day([1-9]|1[0-4])$")


def region_map(snapshot: dict | None = None) -> dict[str, str]:
    if snapshot is None:
        return {f"day-{i}": f"day{i}" for i in range(1, 4)}
    trip = snapshot.get("tripState", snapshot)
    days = trip.get("days") if isinstance(trip, dict) else None
    if isinstance(days, dict):
        numbers = sorted(int(match.group(1)) for key in days if (match := _DAY_KEY.fullmatch(str(key))))
        return {f"day-{number}": f"day{number}" for number in numbers}
    regions = snapshot.get("uiSpec", {}).get("regions")
    if isinstance(regions, dict):
        numbers = sorted(int(key.split("-")[1]) for key in regions if re.fullmatch(REGION_PATTERN, str(key)))
        return {f"day-{number}": f"day{number}" for number in numbers}
    return region_map()


def day_label(number: int) -> str:
    digits = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九")
    value = digits[number] if number < 10 else "十" + (digits[number - 10] if number > 10 else "")
    return f"第{value}天"


def day_names(snapshot: dict | None = None) -> dict[str, str]:
    return {region: day_label(int(region.split("-")[1])) for region in region_map(snapshot)}


def session_catalog(snapshot: dict | None = None) -> dict:
    catalog = snapshot.get("catalog") if isinstance(snapshot, dict) else None
    if isinstance(catalog, dict) and isinstance(catalog.get("places"), list) and isinstance(catalog.get("hotels"), list):
        return deepcopy(catalog)
    return {"places": deepcopy(PLACES), "hotels": deepcopy(HOTELS), "version": CATALOG_VERSION, "city": "杭州"}