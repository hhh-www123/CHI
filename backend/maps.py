"""AMap data adapter; no model-generated coordinates, routes, durations or keys.

Official APIs used:
https://lbs.amap.com/api/webservice/guide/api/newroute
https://lbs.amap.com/api/webservice/guide/api/staticmaps
https://lbs.amap.com/api/uri-api/guide/search/search
https://lbs.amap.com/api/uri-api/guide/travel/route
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from .config import get_settings


_BASE = "https://restapi.amap.com"
_JSON_LIMIT = 1024 * 1024
_IMAGE_LIMIT = 2 * 1024 * 1024
_CACHE_LOCK = threading.RLock()
_PACE_LOCK = threading.Lock()
_MIN_INTERVAL = 0.6
_NEXT_REQUEST_AT: dict[str, float] = {}
_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_IMAGE_CACHE: OrderedDict[str, tuple[float, tuple[bytes, str]]] = OrderedDict()
_KNOWN_QUERIES = {"tea-village": "龙井村", "hefang-street": "河坊街", "bamboo-path": "云栖竹径", "wetland": "西溪国家湿地公园"}
_MODE_NAMES = {"transit": "公共交通", "driving": "驾车", "walking": "步行"}


class _RedactMapKey(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "restapi.amap.com" in message:
            record.msg = re.sub(r"([?&]key=)[^&\s\"']+", r"\1[redacted]", message)
            record.args = ()
        return True


# HTTPX's informational request log otherwise includes URL query parameters.
logging.getLogger("httpx").addFilter(_RedactMapKey())


class _Unavailable(Exception):
    """Contains a public explanation only; never an upstream response or URL."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_key(namespace: str, api_key: str, values: Any) -> str:
    value = json.dumps([namespace, api_key, values], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def _cached(key: str, image: bool = False) -> Any:
    cache = _IMAGE_CACHE if image else _CACHE
    with _CACHE_LOCK:
        entry = cache.get(key)
        if not entry:
            return None
        if entry[0] <= time.monotonic():
            del cache[key]
            return None
        cache.move_to_end(key)
        return deepcopy(entry[1])


def _remember(key: str, value: Any, ttl: int, image: bool = False):
    cache = _IMAGE_CACHE if image else _CACHE
    with _CACHE_LOCK:
        cache[key] = (time.monotonic() + ttl, deepcopy(value))
        cache.move_to_end(key)
        while len(cache) > (24 if image else 384):
            cache.popitem(last=False)


def _request_once(client: httpx.Client, path: str, params: dict[str, str], *, image: bool = False) -> Any:
    """Fetch from a fixed provider with a streaming byte ceiling and no redirects."""
    limit = _IMAGE_LIMIT if image else _JSON_LIMIT
    try:
        options: dict[str, Any] = {}
        deadline = getattr(client, "_map_deadline", None)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Unavailable("本轮地图查询已达到时限；未查询的路线保持待确认。")
            options["timeout"] = min(client.timeout.read or 8, remaining)
        with client.stream("GET", _BASE + path, params=params, **options) as response:
            if response.status_code == 429:
                raise _RateLimited("地图服务正限流，请稍后刷新。")
            if response.status_code != 200:
                raise _Unavailable("地图服务暂时不可用，请稍后再试。")
            content = bytearray()
            for part in response.iter_bytes():
                content.extend(part)
                if len(content) > limit:
                    raise _Unavailable("地图服务响应过大，已停止处理。")
            if image:
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                raw = bytes(content)
                signatures = {
                    "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
                    "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
                    "image/gif": raw.startswith((b"GIF87a", b"GIF89a")),
                }
                if not signatures.get(content_type, False):
                    raise _Unavailable("地图图片暂不可用，请检查地图服务配置和配额。")
                return raw, content_type
            result = json.loads(content)
            if isinstance(result, dict) and str(result.get("infocode")) in {"10014", "10019", "10020", "10021", "10022", "10023"}:
                raise _RateLimited("地图服务正限流，请稍后刷新。")
            if not isinstance(result, dict) or str(result.get("status")) != "1":
                raise _Unavailable("地图查询未成功，请检查地图服务配置和配额。")
            return result
    except _Unavailable:
        raise
    except (httpx.HTTPError, ValueError, TypeError, OSError):
        raise _Unavailable("地图查询超时或暂不可用；没有使用猜测的路线。") from None


class _RateLimited(_Unavailable):
    pass


def _pace(client: httpx.Client, key: str):
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    with _PACE_LOCK:
        now = time.monotonic()
        wait = max(0, _NEXT_REQUEST_AT.get(fingerprint, 0) - now)
        deadline = getattr(client, "_map_deadline", None)
        if deadline is not None and now + wait >= deadline:
            raise _Unavailable("本轮地图查询已达到时限，请稍后刷新未完成路段。")
        if wait:
            time.sleep(wait)
        _NEXT_REQUEST_AT[fingerprint] = time.monotonic() + _MIN_INTERVAL
        if len(_NEXT_REQUEST_AT) > 16:
            oldest = min(_NEXT_REQUEST_AT, key=_NEXT_REQUEST_AT.get)
            del _NEXT_REQUEST_AT[oldest]


def _request(client: httpx.Client, path: str, params: dict[str, str], *, image: bool = False) -> Any:
    for attempt in range(2):
        _pace(client, params.get("key", ""))
        try:
            return _request_once(client, path, params, image=image)
        except _RateLimited:
            if attempt:
                raise _Unavailable("地图服务仍处于限流状态，请稍后刷新；已选行程会保留。") from None
            deadline = getattr(client, "_map_deadline", None)
            if deadline is not None and time.monotonic() + 0.8 >= deadline:
                raise _Unavailable("地图查询正在限流，请稍后刷新未完成路段。") from None
            time.sleep(0.8)
    raise _Unavailable("地图暂不可用，请稍后重试。")


def _normal_name(value: str) -> str:
    value = re.sub(r"[（(].*?[)）]", "", value)
    value = re.sub(r"[\s·\-]", "", value)
    return re.sub(r"(?:风景名胜区|风景区|景区)$", "", value)


def _coordinate(value: Any) -> tuple[float, float] | None:
    try:
        parts = value.split(",") if isinstance(value, str) else value
        if not isinstance(parts, (list, tuple)) or len(parts) != 2:
            return None
        x, y = float(parts[0]), float(parts[1])
        if math.isfinite(x) and math.isfinite(y) and 73 <= x <= 136 and 17 <= y <= 54:
            return round(x, 6), round(y, 6)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _point_text(point: dict) -> str:
    return f"{point['lng']:.6f},{point['lat']:.6f}"


def _search_url(name: str, city: str) -> str:
    return "https://uri.amap.com/search?" + urlencode({"keyword": name, "city": city, "view": "map", "src": "travel-genui", "callnative": "0"})


def _code(value: Any) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{3,6}", value) else ""


def _city_code(client: httpx.Client, key: str, city: str) -> str:
    cache_key = _cache_key("city-code", key, city)
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    result = _request(client, "/v3/config/district", {"key": key, "keywords": city, "subdistrict": "0", "extensions": "base", "output": "JSON"})
    matches = [item for item in result.get("districts", []) if isinstance(item, dict) and str(item.get("name", "")).rstrip("市") == city.rstrip("市") and _code(item.get("citycode"))]
    code = _code(matches[0].get("citycode")) if len(matches) == 1 else ""
    _remember(cache_key, code, 86400 if code else 120)
    return code


def _resolve(client: httpx.Client, key: str, place: dict, city: str) -> dict:
    name = str(place.get("name", "未命名景点"))
    base = {"name": name, "status": "unresolved", "searchUrl": _search_url(name, city)}
    if place.get("fictional") or "虚构" in str(place.get("description", "")):
        return {**base, "status": "fictional"}
    query = str(place.get("mapQuery") or _KNOWN_QUERIES.get(str(place.get("id")), name)).strip()
    if not place.get("mapQuery") and any(word in query for word in ["湖畔", "步道", "沿岸", "附近"]):
        return base
    aliases = { _normal_name(value) for value in [name, query, *place.get("mapAliases", [])] if isinstance(value, str) }
    # City catalogs have a verified administrative identity. County-level cities
    # may use their parent prefecture in the provider's cityname field.
    catalog_adcode = str(place.get("adcode", ""))
    verified_adcode = catalog_adcode if (place.get("livePoiVerified") is True and place.get("mapPoiId")
                                       and re.fullmatch(r"\d{6}", catalog_adcode)) else ""
    cache_key = _cache_key("poi-v3", key, [query, city, place.get("mapPoiId"), sorted(aliases), place.get("address"), verified_adcode])
    cached = _cached(cache_key)
    if cached is not None:
        return {**base, **cached}
    try:
        poi_id = place.get("mapPoiId")
        if poi_id:
            response = _request(client, "/v3/place/detail", {"key": key, "id": str(poi_id), "extensions": "all", "output": "JSON"})
        else:
            response = _request(client, "/v3/place/text", {"key": key, "keywords": query, "city": city, "citylimit": "true", "offset": "10", "page": "1", "extensions": "all", "output": "JSON"})
        pois = response.get("pois", [])
        matches = []
        for poi in pois[:10] if isinstance(pois, list) else []:
            if not isinstance(poi, dict):
                continue
            same_id = bool(poi_id) and poi.get("id") == poi_id
            same_name = _normal_name(str(poi.get("name", ""))) in aliases
            poi_city = poi.get("cityname") if isinstance(poi.get("cityname"), str) else ""
            same_city = (str(poi.get("adcode", "")) == verified_adcode if verified_adcode
                         else not poi_city or poi_city.rstrip("市") == city.rstrip("市"))
            coordinate = _coordinate(poi.get("location"))
            # An explicit, verified provider ID is authoritative; never replace it
            # with a same-name entrance, car park, bus stop or a different branch.
            if coordinate and same_city and (same_id if poi_id else same_name):
                matches.append((poi, coordinate))
        unique = {(xy[0], xy[1]): (poi, xy) for poi, xy in matches}
        if len(unique) != 1:
            result = {"status": "unresolved"}
        else:
            poi, xy = next(iter(unique.values()))
            city_code = _code(poi.get("citycode"))
            if not city_code:
                try:
                    city_code = _city_code(client, key, city)
                except _Unavailable:
                    city_code = ""
            result = {"status": "resolved", "lng": xy[0], "lat": xy[1], "poiId": str(poi.get("id", "")), "cityCode": city_code, "adCode": _code(poi.get("adcode")), "matchedName": str(poi.get("name", "")), "matchedAddress": poi.get("address") if isinstance(poi.get("address"), str) else "", "verifiedAt": _now()}
        _remember(cache_key, result, 3600 if result["status"] == "resolved" else 120)
        return {**base, **result}
    except _Unavailable:
        _remember(cache_key, {"status": "unresolved"}, 30)
        return base


def _numeric(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 10000000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _polyline(value: Any) -> list[list[float]]:
    if not isinstance(value, str):
        return []
    result = []
    for chunk in value.split(";")[:10000]:
        coordinate = _coordinate(chunk)
        if not coordinate:
            return []
        if not result or list(coordinate) != result[-1]:
            result.append(list(coordinate))
    return result


def _parts(value: Any) -> list[list[list[float]]]:
    """Collect provider geometry; choose one bus line rather than all alternatives."""
    result: list[list[list[float]]] = []
    if isinstance(value, dict):
        if isinstance(value.get("polyline"), str):
            line = _polyline(value["polyline"])
            if len(line) > 1:
                result.append(line)
        else:
            for key in ("steps", "segments", "walking", "bus", "buslines", "bus_lines", "railway"):
                child = value.get(key)
                if key in {"buslines", "bus_lines"} and isinstance(child, list):
                    child = child[:1]
                result.extend(_parts(child))
    elif isinstance(value, list):
        for child in value[:100]:
            result.extend(_parts(child))
    return result


def _connected_path(parts: list[list[list[float]]]) -> list[list[float]]:
    result: list[list[float]] = []
    for line in parts:
        if result and math.hypot(result[-1][0] - line[0][0], result[-1][1] - line[0][1]) > .00005:
            # The UI contract has a single polyline. Do not draw straight bridges
            # across missing transit geometry; metrics remain usable without it.
            return []
        result.extend(line if not result else line[1:] if line[0] == result[-1] else line)
        if len(result) > 10000:
            return []
    return result


def _transit_lines(candidate: dict) -> list[str]:
    names = []
    for segment in candidate.get("segments", [])[:50]:
        if not isinstance(segment, dict):
            continue
        bus = segment.get("bus")
        if not isinstance(bus, dict):
            continue
        lines = bus.get("buslines") or bus.get("bus_lines") or []
        if isinstance(lines, list) and lines and isinstance(lines[0], dict):
            name = str(lines[0].get("name", ""))[:100]
            if name and name not in names:
                names.append(name)
    return names[:5]


def _navigation(origin: dict, destination: dict, mode: str) -> str:
    return "https://uri.amap.com/navigation?" + urlencode({
        "from": _point_text(origin) + "," + origin["name"],
        "to": _point_text(destination) + "," + destination["name"],
        "mode": {"driving": "car", "walking": "walk", "transit": "bus"}[mode],
        "src": "travel-genui", "callnative": "0",
    })


def _segment(client: httpx.Client, key: str, origin: dict, destination: dict, mode: str, date: str) -> dict:
    result = {"fromStopId": origin["stopId"], "toStopId": destination["stopId"], "fromName": origin["name"], "toName": destination["name"], "mode": mode, "status": "unavailable", "durationMinutes": None, "distanceMeters": None, "summary": "起点或终点尚未确认，无法查询真实交通方案。", "path": [], "navigationUrl": None}
    if origin["status"] != "resolved" or destination["status"] != "resolved":
        return result
    result["navigationUrl"] = _navigation(origin, destination, mode)
    cache_key = _cache_key("route", key, [origin.get("lng"), origin.get("lat"), destination.get("lng"), destination.get("lat"), mode, date])
    cached = _cached(cache_key)
    if cached is not None:
        return {**result, **cached}
    params = {"key": key, "origin": _point_text(origin), "destination": _point_text(destination), "show_fields": "cost,polyline", "output": "JSON"}
    path = "/v5/direction/" + mode
    if mode == "transit":
        if not origin.get("cityCode") or not destination.get("cityCode"):
            return {**result, "summary": "城市代码尚未确认，暂不能查询公共交通。可在高德地图中继续确认。"}
        path = "/v5/direction/transit/integrated"
        params.update(city1=origin["cityCode"], city2=destination["cityCode"], strategy="0", AlternativeRoute="1")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            params["date"] = date
    try:
        response = _request(client, path, params)
        route = response.get("route", {})
        candidates = route.get("transits" if mode == "transit" else "paths", []) if isinstance(route, dict) else []
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            if mode == "transit":
                walking = _segment(client, key, origin, destination, "walking", date)
                if walking["status"] == "ready":
                    detail = {field: walking[field] for field in ("status", "mode", "durationMinutes", "distanceMeters", "path", "navigationUrl")}
                    detail["summary"] = "未查到可用公交方案，以下为高德步行路线。"
                    _remember(cache_key, detail, 120)
                    return {**result, **detail}
            raise _Unavailable("该出行方式暂未查到路线，可在高德地图中继续确认。")
        candidate = candidates[0]
        cost = candidate.get("cost") if isinstance(candidate.get("cost"), dict) else {}
        duration = _numeric(cost.get("duration", candidate.get("duration")))
        distance = _numeric(candidate.get("distance"))
        if duration is None or distance is None:
            raise _Unavailable("地图服务未返回完整时长与距离，暂不展示估算。")
        names = _transit_lines(candidate) if mode == "transit" else []
        detail = {"status": "ready", "durationMinutes": round(duration / 60, 1), "distanceMeters": round(distance), "summary": " → ".join(names) if names else f"高德{_MODE_NAMES[mode]}方案；实际耗时受出发时间及路况影响。", "path": _connected_path(_parts(candidate))}
        _remember(cache_key, detail, 600)
        return {**result, **detail}
    except _Unavailable as exc:
        detail = {"summary": str(exc)}
        _remember(cache_key, detail, 30)
        return {**result, **detail}


def enrich_day(day: dict, places_by_id: dict[str, dict], city: str, mode: str) -> dict:
    """Resolve trusted catalog POIs and query one mode between adjacent stops."""
    if mode not in _MODE_NAMES:
        raise HTTPException(400, "交通方式只支持公共交通、驾车和步行。")
    settings = get_settings()
    stops = day.get("stops", [])[:20]
    points = []
    with httpx.Client(timeout=settings.map_timeout, follow_redirects=False, trust_env=False) as client:
        client._map_deadline = time.monotonic() + max(5, settings.map_timeout * 2)
        for stop in stops:
            place = places_by_id.get(stop.get("placeId"), {})
            if settings.amap_key:
                point = _resolve(client, settings.amap_key, place, city)
            else:
                fictional = place.get("fictional") or "虚构" in str(place.get("description", ""))
                point = {"name": str(place.get("name", "未命名景点")), "status": "fictional" if fictional else "unresolved", "searchUrl": _search_url(str(place.get("name", "未命名景点")), city)}
            points.append({**point, "stopId": str(stop.get("id", ""))})
        segments = [_segment(client, settings.amap_key, origin, destination, mode, str(day.get("date", ""))) for origin, destination in zip(points, points[1:])]
    ready = sum(segment["status"] == "ready" for segment in segments)
    resolved = sum(point["status"] == "resolved" for point in points)
    if not settings.amap_key:
        status, message = "unconfigured", "地图服务尚未配置。可以打开景点搜索；真实交通方式、时长与路线将在地图服务连接后查询。"
    elif points and resolved == len(points) and ready == len(segments):
        status, message = "ready", "位置与交通数据来自高德地图；时长为查询时的路线方案，请出行前再次确认。"
    elif ready or resolved:
        status, message = "partial", "部分位置或路线尚未确认，请查看对应路段提示；未查到的数据没有进行估算。"
    else:
        status, message = "unavailable", "尚无可确认的位置或路线。请确认景点名称、地图服务配置及可用配额。"
    return {"status": status, "message": message, "provider": "amap", "queriedAt": _now(), "points": points, "segments": segments}


def static_map_image(route_day: dict) -> tuple[bytes, str]:
    """Proxy an AMap static preview; the secret never becomes a browser URL."""
    settings = get_settings()
    if not settings.amap_key:
        raise HTTPException(503, "地图服务尚未配置，请设置后端 AMAP_KEY。")
    if route_day.get("provider") != "amap":
        raise HTTPException(400, "当前地图数据来源不受支持。")
    points = [p for p in route_day.get("points", []) if isinstance(p, dict) and p.get("status") == "resolved" and _coordinate([p.get("lng"), p.get("lat")])]
    if not points:
        raise HTTPException(404, "没有已确认的位置，暂不能显示地图。")
    params = {"key": settings.amap_key, "size": "640*320", "scale": "1", "markers": "|".join(f"mid,0x245846,{index + 1}:" + _point_text(point) for index, point in enumerate(points[:10]))}
    paths = []
    for segment in route_day.get("segments", [])[:4]:
        if not isinstance(segment, dict) or segment.get("status") != "ready":
            continue
        path = segment.get("path", [])
        if not isinstance(path, list) or len(path) < 2 or any(not _coordinate(p) for p in path):
            continue
        # Static previews have a bounded URL budget. Samples are still provider
        # coordinates; no straight line is created merely from start/end POIs.
        stride = max(1, math.ceil(len(path) / 80))
        sampled = path[::stride]
        if sampled[-1] != path[-1]:
            sampled.append(path[-1])
        paths.append("5,0x245846,1,,:" + ";".join(f"{p[0]:.6f},{p[1]:.6f}" for p in sampled))
    if paths:
        params["paths"] = "|".join(paths)
    cache_key = _cache_key("static", settings.amap_key, params)
    cached = _cached(cache_key, image=True)
    if cached is not None:
        return cached
    try:
        with httpx.Client(timeout=settings.map_timeout, follow_redirects=False, trust_env=False) as client:
            result = _request(client, "/v3/staticmap", params, image=True)
        _remember(cache_key, result, 300, image=True)
        return result
    except _Unavailable as exc:
        raise HTTPException(502, str(exc)) from None
