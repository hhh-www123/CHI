"""Provider-boundary tests use HTTP fixtures, never real keys or paid requests."""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from backend.config import Settings
from backend import maps


_CLIENT = httpx.Client
_SECRET = "maps-test-secret-never-public"
PLACES = {
    "a": {"id": "a", "name": "甲景点", "description": "真实景点测试数据。"},
    "b": {"id": "b", "name": "乙景点", "description": "真实景点测试数据。"},
    "fake": {"id": "fake", "name": "虚构茶馆", "description": "虚构室内体验点，仅课程演示。", "mapQuery": "真实茶馆"},
    "broad": {"id": "broad", "name": "西湖湖畔", "description": "沿湖慢行。"},
}
DAY = {"date": "2026-10-24", "stops": [{"id": "stop-a", "placeId": "a"}, {"id": "stop-b", "placeId": "b"}]}


class MapsTests(unittest.TestCase):
    def setUp(self):
        pace_patch = patch.object(maps, "_MIN_INTERVAL", 0)
        pace_patch.start()
        self.addCleanup(pace_patch.stop)
        maps._NEXT_REQUEST_AT.clear()
        maps._CACHE.clear()
        maps._IMAGE_CACHE.clear()
        self.settings = Settings(amap_key=_SECRET, map_timeout=2)
        self.requests: list[httpx.Request] = []

    def mock(self, handler=None):
        def route(request):
            self.requests.append(request)
            return (handler or self.good_response)(request)

        self.addCleanup(patch.stopall)
        patch("backend.maps.get_settings", return_value=self.settings).start()
        patch("backend.maps.httpx.Client", side_effect=lambda **kw: _CLIENT(transport=httpx.MockTransport(route), **kw)).start()

    def good_response(self, request):
        if request.url.path == "/v3/place/text":
            name = request.url.params["keywords"]
            location = "120.100000,30.100000" if name == "甲景点" else "120.110000,30.110000"
            return httpx.Response(200, json={"status": "1", "pois": [{"id": name, "name": name, "location": location, "cityname": "杭州市", "citycode": "0571", "adcode": "330106"}]})
        if request.url.path == "/v5/direction/transit/integrated":
            return httpx.Response(200, json={"status": "1", "route": {"transits": [{"distance": "2200", "cost": {"duration": "1200"}, "segments": [{"bus": {"buslines": [{"name": "地铁测试线", "polyline": "120.100000,30.100000;120.105000,30.105000;120.110000,30.110000"}]}}]}]}})
        if request.url.path in {"/v5/direction/walking", "/v5/direction/driving"}:
            return httpx.Response(200, json={"status": "1", "route": {"paths": [{"distance": "1600", "cost": {"duration": "600"}, "steps": [{"polyline": "120.100000,30.100000;120.105000,30.105000"}, {"polyline": "120.105000,30.105000;120.110000,30.110000"}]}]}})
        if request.url.path == "/v3/staticmap":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfixture", headers={"content-type": "image/png"})
        raise AssertionError("Unexpected provider path " + request.url.path)

    def test_unconfigured_exposes_search_only_and_makes_no_requests(self):
        self.settings = replace(self.settings, amap_key="")
        self.mock()
        result = maps.enrich_day(DAY, PLACES, "杭州", "transit")
        self.assertEqual("unconfigured", result["status"])
        self.assertEqual([], self.requests)
        self.assertEqual(1, len(result["segments"]))
        self.assertIsNone(result["segments"][0]["durationMinutes"])
        self.assertIn("uri.amap.com/search?", result["points"][0]["searchUrl"])
        self.assertNotIn(_SECRET, json.dumps(result))

    def test_virtual_and_broad_place_are_never_guessed(self):
        self.mock()
        day = {"stops": [{"id": "virtual", "placeId": "fake"}, {"id": "wide", "placeId": "broad"}]}
        result = maps.enrich_day(day, PLACES, "杭州", "walking")
        self.assertEqual([], self.requests)
        self.assertEqual(["fictional", "unresolved"], [p["status"] for p in result["points"]])
        self.assertEqual([], result["segments"][0]["path"])
        self.assertNotIn("lng", result["points"][0])

    def test_real_driving_data_is_parsed_and_cached_without_exposing_key(self):
        self.mock()
        result = maps.enrich_day(DAY, PLACES, "杭州", "driving")
        self.assertEqual("ready", result["status"])
        segment = result["segments"][0]
        self.assertEqual(10, segment["durationMinutes"])
        self.assertEqual(1600, segment["distanceMeters"])
        self.assertEqual(3, len(segment["path"]))
        self.assertIn("uri.amap.com/navigation?", segment["navigationUrl"])
        self.assertIn("mode=car", segment["navigationUrl"])
        maps.enrich_day(DAY, PLACES, "杭州", "driving")
        self.assertEqual(3, len(self.requests), "Repeated query should use bounded provider cache")
        self.assertTrue(all(request.url.params["extensions"] == "all" for request in self.requests if request.url.path == "/v3/place/text"))
        self.assertNotIn(_SECRET, json.dumps(result, ensure_ascii=False))

    def test_transit_uses_provider_city_codes_and_actual_bus_line(self):
        self.mock()
        result = maps.enrich_day(DAY, PLACES, "杭州", "transit")
        segment = result["segments"][0]
        self.assertEqual("ready", segment["status"])
        self.assertEqual(20, segment["durationMinutes"])
        self.assertEqual("地铁测试线", segment["summary"])
        request = self.requests[-1]
        self.assertEqual("0571", request.url.params["city1"])
        self.assertEqual("0571", request.url.params["city2"])

    def test_ambiguous_or_wrong_search_result_is_not_used(self):
        def handler(request):
            return httpx.Response(200, json={"status": "1", "pois": [{"name": "完全不同的景点", "cityname": "杭州市", "location": "120.1,30.1"}]})
        self.mock(handler)
        result = maps.enrich_day(DAY, PLACES, "杭州", "walking")
        self.assertEqual("unavailable", result["status"])
        self.assertTrue(all(p["status"] == "unresolved" for p in result["points"]))
        self.assertTrue(all(r.url.path == "/v3/place/text" for r in self.requests))

    def test_upstream_failures_are_safe_and_do_not_invent_metrics(self):
        def handler(request):
            if request.url.path == "/v3/place/text":
                return self.good_response(request)
            return httpx.Response(403, json={"secret": _SECRET})
        self.mock(handler)
        result = maps.enrich_day(DAY, PLACES, "杭州", "walking")
        segment = result["segments"][0]
        self.assertEqual("partial", result["status"])
        self.assertEqual("unavailable", segment["status"])
        self.assertIsNone(segment["durationMinutes"])
        self.assertEqual([], segment["path"])
        self.assertNotIn(_SECRET, json.dumps(result))

    def test_static_map_is_server_proxied_and_has_only_provider_geometry(self):
        self.mock()
        result = maps.enrich_day(DAY, PLACES, "杭州", "walking")
        image, content_type = maps.static_map_image(result)
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertEqual("image/png", content_type)
        request = self.requests[-1]
        self.assertEqual("640*320", request.url.params["size"])
        self.assertIn("120.105000,30.105000", request.url.params["paths"])
        self.assertEqual(_SECRET, request.url.params["key"])
        maps.static_map_image(result)
        self.assertEqual(4, len(self.requests), "Static images should be cached independently")

    def test_disconnected_provider_geometry_has_no_invented_straight_bridges(self):
        def handler(request):
            if request.url.path == "/v5/direction/walking":
                return httpx.Response(200, json={"status": "1", "route": {"paths": [{"distance": "1600", "cost": {"duration": "600"}, "steps": [{"polyline": "120.10,30.10;120.101,30.101"}, {"polyline": "120.109,30.109;120.11,30.11"}]}]}})
            return self.good_response(request)
        self.mock(handler)
        result = maps.enrich_day(DAY, PLACES, "杭州", "walking")
        self.assertEqual("ready", result["segments"][0]["status"])
        self.assertEqual([], result["segments"][0]["path"])
        maps.static_map_image(result)
        self.assertNotIn("paths", self.requests[-1].url.params)

    def test_static_errors_and_non_image_responses_do_not_leak_secrets(self):
        self.mock(lambda request: httpx.Response(200, json={"status": "0", "key": _SECRET}))
        route = {"provider": "amap", "points": [{"status": "resolved", "lng": 120.1, "lat": 30.1}], "segments": []}
        with self.assertRaises(HTTPException) as caught:
            maps.static_map_image(route)
        self.assertEqual(502, caught.exception.status_code)
        self.assertNotIn(_SECRET, caught.exception.detail)
        with self.assertRaises(HTTPException) as caught:
            maps.static_map_image({"provider": "amap", "points": []})
        self.assertEqual(404, caught.exception.status_code)

    def test_httpx_info_logs_redact_provider_query_key(self):
        record = logging.LogRecord("httpx", logging.INFO, "", 1, "HTTP Request: GET %s", (f"https://restapi.amap.com/v3/place/text?key={_SECRET}&city=test",), None)
        maps._RedactMapKey().filter(record)
        self.assertNotIn(_SECRET, record.getMessage())
        self.assertIn("[redacted]", record.getMessage())


    def test_verified_poi_id_and_missing_city_code_are_resolved_from_provider(self):
        places = {"a": {"id": "a", "name": "甲景点", "mapPoiId": "poi-a"}, "b": {"id": "b", "name": "乙景点", "mapPoiId": "poi-b"}}
        def handler(request):
            if request.url.path == "/v3/place/detail":
                pid = request.url.params["id"]
                return httpx.Response(200, json={"status": "1", "pois": [{"id": pid, "name": "甲景点" if pid == "poi-a" else "乙景点", "cityname": "杭州市", "location": "120.1,30.1" if pid == "poi-a" else "120.11,30.11"}]})
            if request.url.path == "/v3/config/district":
                return httpx.Response(200, json={"status": "1", "districts": [{"name": "杭州市", "citycode": "0571"}]})
            return self.good_response(request)
        self.mock(handler)
        result = maps.enrich_day(DAY, places, "杭州", "transit")
        self.assertEqual("ready", result["status"])
        self.assertEqual(["0571", "0571"], [p["cityCode"] for p in result["points"]])
        self.assertEqual(1, sum(r.url.path == "/v3/config/district" for r in self.requests))
        self.assertFalse(any(r.url.path == "/v3/place/text" for r in self.requests))

    def test_wrong_explicit_poi_id_never_falls_back_to_unrelated_name(self):
        self.mock(lambda request: httpx.Response(200, json={"status": "1", "pois": [{"id": "wrong-id", "name": "甲景点", "cityname": "杭州市", "location": "120.1,30.1"}]}))
        day = {"stops": [{"id": "stop-a", "placeId": "a"}]}
        result = maps.enrich_day(day, {"a": {"id": "a", "name": "甲景点", "mapPoiId": "correct-id"}}, "杭州", "walking")
        self.assertEqual("unavailable", result["status"])
        self.assertEqual("unresolved", result["points"][0]["status"])
        self.assertNotIn("lng", result["points"][0])

    def test_provider_qps_limit_is_retried_once_then_recovers(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            if count == 1:
                return httpx.Response(200, json={"status": "0", "infocode": "10021"})
            return self.good_response(request)
        self.mock(handler)
        with patch.object(maps.time, "sleep"):
            result = maps.enrich_day(DAY, PLACES, "杭州", "walking")
        self.assertEqual("ready", result["status"])
        self.assertEqual(4, len(self.requests))

    def test_verified_county_poi_uses_exact_adcode_despite_parent_city_name(self):
        place = {"id": "county", "name": "亭林园", "mapPoiId": "poi-kunshan", "adcode": "320583", "livePoiVerified": True}
        def handler(request):
            self.assertEqual(request.url.path, "/v3/place/detail")
            self.assertEqual(request.url.params["extensions"], "all")
            return httpx.Response(200, json={"status": "1", "pois": [{"id": "poi-kunshan", "name": "亭林园", "cityname": "苏州市", "citycode": "0512", "adcode": "320583", "location": "120.95,31.39"}]})
        self.mock(handler)
        day = {"stops": [{"id": "county-stop", "placeId": "county"}]}
        result = maps.enrich_day(day, {"county": place}, "昆山市", "walking")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["points"][0]["status"], "resolved")
        self.assertEqual(result["points"][0]["adCode"], "320583")
        self.assertEqual(result["points"][0]["cityCode"], "0512")
        self.assertEqual(len(self.requests), 1)
        maps.enrich_day(day, {"county": place}, "昆山市", "walking")
        self.assertEqual(len(self.requests), 1, "matching administrative identity can reuse its cache")
        changed = {**place, "adcode": "320582"}
        denied = maps.enrich_day(day, {"county": changed}, "昆山市", "walking")
        self.assertEqual(denied["points"][0]["status"], "unresolved")
        self.assertEqual(len(self.requests), 2, "a different expected adcode must not reuse a resolved cache")

    def test_verified_county_poi_rejects_missing_wrong_adcode_or_wrong_provider_id(self):
        changes = {}
        def handler(request):
            poi = {"id": "poi-kunshan", "name": "亭林园", "cityname": "苏州市", "citycode": "0512", "adcode": "320583", "location": "120.95,31.39", **changes}
            return httpx.Response(200, json={"status": "1", "pois": [poi]})
        self.mock(handler)
        places = {"county": {"id": "county", "name": "亭林园", "mapPoiId": "poi-kunshan", "adcode": "320583", "livePoiVerified": True}}
        day = {"stops": [{"id": "county-stop", "placeId": "county"}]}
        for values in ({"adcode": "320582"}, {"adcode": None}, {"id": "different-provider-poi"}):
            with self.subTest(values=values):
                changes.clear()
                changes.update(values)
                maps._CACHE.clear()
                result = maps.enrich_day(day, places, "昆山市", "walking")
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["points"][0]["status"], "unresolved")
                self.assertNotIn("lng", result["points"][0])

    def test_unverified_or_legacy_county_catalog_keeps_original_city_name_check(self):
        self.mock(lambda request: httpx.Response(200, json={"status": "1", "pois": [{"id": "poi-kunshan", "name": "亭林园", "cityname": "苏州市", "citycode": "0512", "adcode": "320583", "location": "120.95,31.39"}]}))
        base = {"id": "county", "name": "亭林园", "mapPoiId": "poi-kunshan"}
        day = {"stops": [{"id": "county-stop", "placeId": "county"}]}
        for place in (base, {**base, "adcode": "320583", "livePoiVerified": False}):
            with self.subTest(place=place):
                maps._CACHE.clear()
                result = maps.enrich_day(day, {"county": place}, "昆山市", "walking")
                self.assertEqual(result["points"][0]["status"], "unresolved")
    def test_catalog_only_exposes_verified_real_places_and_estimated_prices(self):
        from backend.catalog import PLACES as real_places, HOTELS
        self.assertEqual(15, len(real_places))
        self.assertEqual(3, len(HOTELS))
        for item in [*real_places, *HOTELS]:
            self.assertTrue(item["livePoiVerified"])
            self.assertFalse(item.get("fictional"))
            self.assertTrue(item["address"])
            self.assertTrue(item["mapPoiId"])
            self.assertTrue(item["sourceUrl"].startswith("https://"))
            self.assertNotIn("lng", item)
            self.assertNotIn("lat", item)
        self.assertTrue(all(hotel["priceEstimated"] for hotel in HOTELS))


    def test_short_trip_without_transit_uses_a_real_walking_route(self):
        def handler(request):
            if request.url.path == "/v5/direction/transit/integrated":
                return httpx.Response(200, json={"status": "1", "route": {"transits": []}})
            return self.good_response(request)
        self.mock(handler)
        result = maps.enrich_day(DAY, PLACES, "杭州", "transit")
        self.assertEqual("ready", result["status"])
        segment = result["segments"][0]
        self.assertEqual("walking", segment["mode"])
        self.assertEqual(10, segment["durationMinutes"])
        self.assertIn("步行", segment["summary"])
        self.assertIn("mode=walk", segment["navigationUrl"])


if __name__ == "__main__":
    unittest.main()
