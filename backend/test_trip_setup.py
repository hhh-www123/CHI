"""Trip setup and city catalogs: provider responses are always mocked."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend import agent, maps, trip_setup as setup
from backend.config import Settings
from backend.domain import day_names, region_map, session_catalog


class DomainTests(unittest.TestCase):
    def test_regions_and_names_follow_saved_days_without_adding_default_dates(self):
        snapshot = {"tripState": {"days": {"day14": {}, "day2": {}, "day1": {}}}}
        self.assertEqual(region_map(snapshot), {"day-1": "day1", "day-2": "day2", "day-14": "day14"})
        self.assertEqual(day_names(snapshot)["day-14"], "第十四天")
        self.assertEqual(region_map({"tripState": {"days": {}}}), {})
        self.assertEqual(len(region_map()), 3)

    def test_catalog_is_session_local_and_returned_without_aliasing(self):
        snapshot = {"catalog": {"places": [{"id": "local"}], "hotels": [{"id": "stay"}], "version": "custom"}}
        chosen = session_catalog(snapshot)
        chosen["places"][0]["id"] = "changed"
        self.assertEqual(snapshot["catalog"]["places"][0]["id"], "local")
        self.assertEqual(session_catalog()["city"], "杭州")

    def test_previous_hangzhou_catalog_is_upgraded_but_custom_catalog_is_not(self):
        legacy = deepcopy(session_catalog())
        legacy["version"] = "hangzhou-curated-20261106"
        legacy["places"] = legacy["places"][:15]
        legacy["hotels"] = legacy["hotels"][:3]
        upgraded = session_catalog({"tripState": {"city": "杭州"}, "catalogVersion": legacy["version"], "catalog": legacy})
        self.assertEqual(upgraded["version"], session_catalog()["version"])
        self.assertEqual(len(upgraded["places"]), 27)
        self.assertEqual(len(upgraded["hotels"]), 12)
        custom = {"places": [{"id": "local"}], "hotels": [{"id": "stay"}], "version": "custom"}
        self.assertEqual(session_catalog({"tripState": {"city": "杭州"}, "catalog": custom}), custom)

    def test_agent_region_schema_supports_only_bounded_dynamic_ids(self):
        self.assertEqual(agent.SetUI(kind="set_ui", regionId="day-14", view="timeline").regionId, "day-14")
        for region in ("day-0", "day-15", "day-01", "other"):
            with self.subTest(region=region), self.assertRaises(ValidationError):
                agent.SetUI(kind="set_ui", regionId=region, view="timeline")


class TripSetupTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(agent_mode="llm", llm_api_key="fixture-only-planning", amap_key="")
        self.patcher = patch.object(setup, "get_settings", side_effect=lambda: self.settings)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        with maps._CACHE_LOCK:
            maps._CACHE.clear()
        self.addCleanup(self.clear_cache)
        self.map_guard = patch.object(maps, "_request", side_effect=AssertionError("unmocked map calls are forbidden"))
        self.request_mock = self.map_guard.start()
        self.addCleanup(self.map_guard.stop)

    @staticmethod
    def clear_cache():
        with maps._CACHE_LOCK:
            maps._CACHE.clear()

    def body(self, **values):
        return setup.TripSetupRequest(**{"city": "杭州", "dayCount": 3, "budgetLimit": 3000, "startDate": "2026-12-30", **values})

    def plan(self, body, catalog=None, **values):
        catalog = catalog or session_catalog()
        return setup.JourneyPlan(**{"hotelId": catalog["hotels"][0]["id"], "days": [
            {"title": f"Day {index + 1}", "note": "Keep personal notes", "stops": [{"placeId": catalog["places"][index]["id"], "startTime": "09:30"}]}
            for index in range(body.dayCount)
        ], **values})

    def test_user_input_rejects_invalid_dates_numbers_and_cities(self):
        invalid = [{"city": "  "}, {"city": "杭州\n攻击"}, {"city": "../city"}, {"dayCount": 0}, {"dayCount": 15},
                   {"dayCount": True}, {"dayCount": "3"}, {"travelers": 0}, {"travelers": 11}, {"travelers": "2"},
                   {"budgetLimit": True}, {"budgetLimit": "3000"}, {"budgetLimit": 99}, {"budgetLimit": 100001},
                   {"budgetLimit": float("nan")}, {"startDate": "2026-02-30"}, {"startDate": "9999-12-31", "dayCount": 14}]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.body(**values)
        self.assertEqual(self.body(city="  苏州市  ").city, "苏州市")
        self.assertEqual(date.fromisoformat(setup.TripSetupRequest(city="杭州", dayCount=1, budgetLimit=100).startDate), date.fromisoformat(setup._today()))

    def test_missing_map_key_does_not_replace_other_city_with_hangzhou(self):
        with self.assertRaises(HTTPException) as result:
            setup.discover_catalog(self.body(city="苏州"))
        self.assertEqual(result.exception.status_code, 503)
        self.request_mock.assert_not_called()

    def test_hangzhou_fallback_uses_existing_verified_catalog(self):
        catalog = setup.discover_catalog(self.body())
        self.assertEqual(catalog["places"], session_catalog()["places"])
        self.assertEqual(catalog["warnings"], [])
        self.request_mock.assert_not_called()

    def test_demo_fourteen_days_has_stable_ids_real_places_and_consecutive_dates(self):
        self.settings = Settings(agent_mode="demo", amap_key="configured-but-demo-must-be-offline")
        body = self.body(dayCount=14, travelers=3)
        payload = setup.build_trip_payload(body)
        days = payload["tripState"]["days"]
        self.assertEqual(len(days), 14)
        self.assertEqual(set(payload["uiSpec"]["regions"]), {f"day-{i}" for i in range(1, 15)})
        self.assertEqual(days["day14"]["date"], (date(2026, 12, 30) + timedelta(days=13)).isoformat())
        self.assertEqual(days["day14"]["stops"][0]["id"], "day14-stop-1")
        visited = [stop["placeId"] for day in days.values() for stop in day["stops"]]
        self.assertEqual(len(visited), len(set(visited)))
        self.assertTrue(set(visited).issubset({place["id"] for place in session_catalog()["places"]}))
        self.assertEqual(payload["tripState"]["roomCount"], 2)
        self.assertEqual(payload["tripState"]["nights"], 13)
        self.assertEqual(payload["planningSettings"]["travelers"], 3)
        self.assertEqual(payload["catalogVersion"], payload["catalog"]["version"])
        self.assertTrue(payload["planningSettings"]["warnings"])
        self.request_mock.assert_not_called()

    def test_one_day_trip_needs_zero_hotel_nights_and_retains_budget(self):
        self.settings = Settings(agent_mode="demo")
        payload = setup.build_trip_payload(self.body(dayCount=1, budgetLimit=100))
        self.assertEqual(payload["tripState"]["nights"], 0)
        self.assertEqual(payload["tripState"]["budgetLimit"], 100)
        self.assertEqual(len(payload["tripState"]["days"]), 1)
        self.assertTrue(any("超过总预算" in warning for warning in payload["planningSettings"]["warnings"]))
        self.assertEqual(payload["routeData"], {})

    def test_offline_genui_uses_actual_preferences_budget_and_presentation(self):
        self.settings = Settings(agent_mode="demo")
        relaxed = setup.build_trip_payload(self.body(preferences="喜欢茶文化和自然，节奏轻松", budgetLimit=3000))
        dense = setup.build_trip_payload(self.body(preferences="喜欢历史博物馆，时间表紧凑，自驾", budgetLimit=12000))
        relaxed_ids = [stop["placeId"] for day in relaxed["tripState"]["days"].values() for stop in day["stops"]]
        dense_ids = [stop["placeId"] for day in dense["tripState"]["days"].values() for stop in day["stops"]]
        self.assertNotEqual(relaxed_ids, dense_ids)
        self.assertTrue(all(len(day["stops"]) == 2 for day in relaxed["tripState"]["days"].values()))
        self.assertTrue(all(len(day["stops"]) == 3 for day in dense["tripState"]["days"].values()))
        self.assertTrue(all(region["view"] == "timeline" and region["transportMode"] == "driving" for region in dense["uiSpec"]["regions"].values()))
        self.assertEqual(relaxed["planningSettings"]["generationMode"], "deterministic-demo")
        self.assertIn("偏好", relaxed["planningSettings"]["generationBasis"])
        self.assertNotEqual(relaxed["tripState"]["hotelId"], dense["tripState"]["hotelId"])

    def test_explicit_demo_generation_never_calls_configured_model_or_map(self):
        self.settings = Settings(agent_mode="llm", llm_api_key="configured", amap_key="configured-map")
        with patch.object(agent, "_request_model", side_effect=AssertionError("explicit demo must remain model-free")):
            payload = setup.build_trip_payload(self.body(preferences="雨天室内备选"), force_demo=True)
        self.assertEqual(payload["planningSettings"]["generationMode"], "deterministic-demo")
        self.assertTrue(all(region["showAlternatives"] for region in payload["uiSpec"]["regions"].values()))
        self.request_mock.assert_not_called()

    def test_real_model_planning_receives_preferences_and_only_verified_ids(self):
        body = self.body(dayCount=5, travelers=4, preferences="喜欢文化场馆，慢节奏")
        expected = self.plan(body)
        with patch.object(agent, "_request_model", return_value=expected) as model:
            payload = setup.build_trip_payload(body)
        context, system, schema, phase = model.call_args.args
        self.assertEqual(phase, "trip_plan")
        self.assertIs(schema, setup.JourneyPlan)
        self.assertEqual(context["tripSetup"], body.model_dump())
        self.assertEqual(context["planningRules"]["roomCount"], 2)
        self.assertEqual(payload["tripState"]["budgetLimit"], 3000)
        self.assertEqual(payload["tripState"]["travelers"], 4)
        self.assertEqual(payload["uiSpec"]["regions"]["day-5"]["binding"], "days.day5")
        self.assertIn("catalog.places", system)
        self.request_mock.assert_not_called()

    def test_invalid_model_place_or_hotel_is_repaired_once_then_rejected(self):
        body = self.body()
        for kind in ("place", "hotel", "duplicate", "count"):
            with self.subTest(kind=kind):
                invalid = self.plan(body).model_dump()
                if kind == "place":
                    invalid["days"][0]["stops"][0]["placeId"] = "fabricated-place"
                elif kind == "hotel":
                    invalid["hotelId"] = "fabricated-hotel"
                elif kind == "duplicate":
                    invalid["days"][1]["stops"][0]["placeId"] = invalid["days"][0]["stops"][0]["placeId"]
                else:
                    invalid["days"].pop()
                with patch.object(agent, "_request_model", return_value=setup.JourneyPlan.model_validate(invalid)) as model:
                    with self.assertRaises(HTTPException) as failure:
                        setup.build_trip_payload(body)
                self.assertEqual(failure.exception.status_code, 502)
                self.assertEqual(model.call_count, 2)
                self.assertEqual(model.call_args.args[0]["tripSetup"], body.model_dump())
                self.assertTrue(model.call_args.args[0]["validationFeedback"])

    def test_model_validation_repair_can_complete_without_changing_user_settings(self):
        body = self.body()
        correct = self.plan(body)
        invalid = correct.model_copy(deep=True)
        invalid.hotelId = "unknown"
        with patch.object(agent, "_request_model", side_effect=[invalid, correct]) as model:
            payload = setup.build_trip_payload(body)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(payload["tripState"]["city"], "杭州")
        self.assertEqual(payload["planningSettings"]["startDate"], body.startDate)

    def test_missing_model_key_fails_before_paid_city_search(self):
        self.settings = Settings(agent_mode="llm", amap_key="fixture-map")
        with self.assertRaises(HTTPException) as failure:
            setup.build_trip_payload(self.body(city="苏州"))
        self.assertEqual(failure.exception.status_code, 503)
        self.request_mock.assert_not_called()

    def test_demo_does_not_claim_other_city_is_supported(self):
        self.settings = Settings(agent_mode="demo")
        with self.assertRaises(HTTPException) as failure:
            setup.build_trip_payload(self.body(city="成都"))
        self.assertEqual(failure.exception.status_code, 400)
        self.request_mock.assert_not_called()

    @staticmethod
    def map_reply(_client, path, params):
        if path == "/v3/config/district":
            return {"districts": [{"name": "苏州市", "level": "city", "adcode": "320500", "citycode": "0512", "center": "120.6,31.3"}]}
        common = {"adcode": "320508", "location": "120.61,31.31", "address": "人民路1号", "adname": "姑苏区"}
        if params["types"] == "100000":
            return {"pois": [{**common, "id": "BHOTEL001", "name": "真实旅馆甲", "typecode": "100100"},
                             {**common, "id": "BHOTEL002", "name": "真实旅馆乙", "typecode": "100100"}]}
        if params["types"] == "140000":
            return {"pois": [{**common, "id": "BMUSEUM01", "name": "城内博物馆", "typecode": "140100"}]}
        offset = (int(params["page"]) - 1) * 10
        valid = [{**common, "id": f"BPLACE{index:04d}", "name": f"真实景区{index}", "typecode": "110000"} for index in range(offset, offset + 10)]
        invalid = [{**common, "id": "BWRONG001", "name": "其他城市景区", "typecode": "110000", "adcode": "330102"},
                   {**common, "id": "BWRONG002", "name": "无效坐标景区", "typecode": "110000", "location": "0,0"},
                   {**common, "id": "BWRONG003", "name": "餐馆", "typecode": "050000"},
                   {**common, "id": "BWRONG004", "name": "景区游客中心", "typecode": "110000"}, deepcopy(valid[0])]
        return {"pois": [*valid, *invalid]}

    def test_city_search_filters_pois_and_keeps_provider_identity_and_estimated_prices(self):
        self.settings = Settings(amap_key="fixture-map")
        self.request_mock.side_effect = self.map_reply
        catalog = setup.discover_catalog(self.body(city="苏州", dayCount=5, travelers=3))
        self.assertEqual(catalog["city"], "苏州市")
        self.assertEqual(catalog["adcode"], "320500")
        self.assertEqual(len(catalog["places"]), 21)
        self.assertEqual(len(catalog["hotels"]), 2)
        self.assertEqual(len({place["id"] for place in catalog["places"]}), 21)
        for place in catalog["places"]:
            self.assertTrue(place["livePoiVerified"])
            self.assertTrue(place["adcode"].startswith("3205"))
            self.assertFalse(place["fictional"])
            self.assertNotIn("BWRONG", place["id"])
            self.assertEqual(place["sourceUrl"], "https://www.amap.com/place/" + place["mapPoiId"])
            self.assertTrue(place["costEstimated"])
        for hotel in catalog["hotels"]:
            self.assertTrue(hotel["priceEstimated"])
            self.assertGreater(hotel["nightlyPrice"], 0)
        requests = self.request_mock.call_args_list
        self.assertEqual(requests[0].args[1], "/v3/config/district")
        for call in requests[1:]:
            self.assertEqual(call.args[2]["city"], "320500")
            self.assertEqual(call.args[2]["citylimit"], "true")
            self.assertEqual(call.args[2]["offset"], "25")
            self.assertEqual(call.args[2]["extensions"], "all", "adcode/citycode are extended provider fields")
            if call.args[2]["types"] == "140000":
                self.assertEqual(call.args[2]["keywords"], "博物馆", "the provider accepts one search keyword")

    def test_extended_search_returns_administrative_identity_needed_for_verified_catalog(self):
        self.settings = Settings(amap_key="fixture-map")
        def reply(client, path, params):
            response = self.map_reply(client, path, params)
            if path == "/v3/place/text" and params.get("extensions") != "all":
                for poi in response.get("pois", []):
                    poi.pop("adcode", None)
            return response
        self.request_mock.side_effect = reply
        catalog = setup.discover_catalog(self.body(city="苏州", dayCount=4))
        self.assertGreaterEqual(len(catalog["places"]), 4)
        self.assertTrue(catalog["hotels"])
        self.assertTrue(all(place["adcode"].startswith("3205") for place in catalog["places"]))
        self.assertEqual(self.request_mock.call_args_list[0].args[2]["extensions"], "base", "district lookup remains a separate contract")

    def test_missing_poi_admin_codes_are_not_inferred_from_requested_city(self):
        self.settings = Settings(amap_key="fixture-map")
        def reply(client, path, params):
            response = self.map_reply(client, path, params)
            if path == "/v3/place/text":
                self.assertEqual(params["extensions"], "all")
                for poi in response.get("pois", []):
                    poi.pop("adcode", None)
            return response
        self.request_mock.side_effect = reply
        with self.assertRaises(HTTPException) as failure:
            setup.discover_catalog(self.body(city="苏州", dayCount=4))
        self.assertEqual(failure.exception.status_code, 400)
    def test_ambiguous_or_province_only_city_is_rejected(self):
        self.settings = Settings(amap_key="fixture-map")
        self.request_mock.side_effect = None
        self.request_mock.return_value = {"districts": [{"name": "浙江省", "level": "province", "adcode": "330000", "center": "120,30"}]}
        with self.assertRaises(HTTPException) as failure:
            setup.discover_catalog(self.body(city="浙江省"))
        self.assertEqual(failure.exception.status_code, 400)
        self.assertEqual(self.request_mock.call_count, 1)

    def test_map_failure_never_returns_hangzhou_for_new_city(self):
        self.settings = Settings(amap_key="fixture-map")
        self.request_mock.side_effect = maps._Unavailable("地图查询暂不可用")
        with self.assertRaises(HTTPException) as failure:
            setup.discover_catalog(self.body(city="苏州"))
        self.assertEqual(failure.exception.status_code, 502)
        self.assertNotIn("fixture-map", str(failure.exception.detail))

    def test_small_catalog_cannot_be_padded_with_invented_places(self):
        self.settings = Settings(amap_key="fixture-map")
        self.request_mock.side_effect = lambda _client, path, params: self.map_reply(_client, path, params) if path.endswith("district") or params.get("types") == "100000" else {"pois": []}
        with self.assertRaises(HTTPException) as failure:
            setup.discover_catalog(self.body(city="苏州", dayCount=3))
        self.assertEqual(failure.exception.status_code, 400)

    def test_agent_context_uses_session_local_places_hotels_and_regions(self):
        snapshot = {"version": 1, "tripState": {"days": {"day1": {}, "day7": {}}}, "uiSpec": {}, "controlState": {},
                    "catalog": {"places": [{"id": "local-place", "name": "Local"}], "hotels": [{"id": "local-hotel", "name": "Stay"}]}}
        context = agent._context(snapshot, "只改第七天", "day-7", [{"id": "wrong-default"}])
        self.assertEqual(context["catalog"][0]["id"], "local-place")
        self.assertEqual(context["hotels"][0]["id"], "local-hotel")
        self.assertEqual(context["availableRegions"], {"day-1": "day1", "day-7": "day7"})


if __name__ == "__main__":
    unittest.main()
