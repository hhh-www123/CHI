"""Direct POI replacement and provider route refresh; no external calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as module
from backend import agent
from backend.config import Settings


class RouteRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_store = module.store
        module.store = module.Store(Path(self.directory.name))
        self.settings = Settings(agent_mode="demo", requests_per_minute=120)
        self.config_patch = patch.object(module, "get_settings", side_effect=lambda: self.settings)
        self.config_patch.start()
        self.map_patch = patch.object(module, "enrich_day", side_effect=self.ready_route)
        self.map_mock = self.map_patch.start()
        self.client = TestClient(module.app)
        response = self.client.post("/api/sessions")
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.url = f"/api/sessions/{self.session['id']}"

    def tearDown(self):
        self.client.close()
        self.map_patch.stop()
        self.config_patch.stop()
        module.store = self.previous_store
        self.directory.cleanup()

    def ready_route(self, day, places, city, mode):
        return {
            "status": "ready", "provider": "amap", "message": "路线已查到", "queriedAt": "2026-10-25T00:00:00Z",
            "points": [{"stopId": stop["id"], "name": places[stop["placeId"]]["name"], "status": "resolved", "searchUrl": "https://uri.amap.com/search"} for stop in day["stops"]],
            "segments": [{"fromStopId": first["id"], "toStopId": second["id"], "fromName": places[first["placeId"]]["name"], "toName": places[second["placeId"]]["name"], "mode": mode, "status": "ready", "durationMinutes": 20, "distanceMeters": 2000, "summary": "测试路线", "path": [], "navigationUrl": None} for first, second in zip(day["stops"], day["stops"][1:])],
        }

    def request(self, suffix, payload, method="post", success=200):
        response = getattr(self.client, method)(self.url + suffix, json={"baseVersion": self.session["version"], **payload})
        self.assertEqual(response.status_code, success, response.text)
        if success == 200 and "tripState" in response.json():
            self.session = response.json()
        return response

    def refresh(self, target="day-2"):
        return self.request("/routes", {"targetRegionId": target})

    def replace(self, value="wetland", success=200):
        return self.request("/direct", {"kind": "replace_stop", "targetRegionId": "day-2", "stopId": "day2-stop-1", "value": value}, "patch", success)

    def test_manual_route_refresh_needs_no_model_and_respects_direct_lock_semantics(self):
        self.request("/locks", {"target": "day-2", "locked": True}, "patch")
        before = deepcopy(self.session)
        with patch.object(agent, "scope_request", side_effect=AssertionError("should not call a model")) as model:
            self.refresh()
        model.assert_not_called()
        self.map_mock.assert_called_once()
        self.assertTrue(self.session["uiSpec"]["regions"]["day-2"]["showMap"])
        self.assertTrue(self.session["uiSpec"]["regions"]["day-2"]["showTransport"])
        self.assertEqual(self.session["controlState"], before["controlState"])
        self.assertEqual(self.session["tripState"], before["tripState"])

    def test_replacement_refreshes_routes_and_preserves_stable_state(self):
        self.request("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "已订午餐，请保留"}, "patch")
        self.refresh()
        before = deepcopy(self.session)
        self.map_mock.reset_mock()
        self.replace()
        self.map_mock.assert_called_once()
        selected_day = self.map_mock.call_args.args[0]
        self.assertEqual(selected_day["stops"][0]["placeId"], "wetland")
        self.assertEqual(selected_day["stops"][0]["id"], "day2-stop-1")
        self.assertEqual(selected_day["stops"][0]["startTime"], before["tripState"]["days"]["day2"]["stops"][0]["startTime"])
        self.assertEqual(self.session["tripState"]["days"]["day2"]["note"], "已订午餐，请保留")
        self.assertEqual(self.session["tripState"]["days"]["day1"], before["tripState"]["days"]["day1"])
        self.assertEqual(self.session["tripState"]["hotelId"], before["tripState"]["hotelId"])
        self.assertEqual(self.session["routeData"]["day-2"]["points"][0]["name"], module.PLACE_BY_ID["wetland"]["name"])

    def test_hidden_routes_do_not_trigger_provider_calls(self):
        self.replace()
        self.map_mock.assert_not_called()
        self.assertNotIn("day-2", self.session["routeData"])

    def test_provider_failure_keeps_user_selection_and_removes_stale_routes(self):
        self.refresh()
        self.map_mock.side_effect = RuntimeError("private-map-key-must-not-appear")
        response = self.replace()
        self.assertEqual(self.session["tripState"]["days"]["day2"]["stops"][0]["placeId"], "wetland")
        self.assertEqual(self.session["routeData"]["day-2"]["status"], "unavailable")
        self.assertEqual(self.session["routeData"]["day-2"]["points"], [])
        self.assertNotIn("private-map-key", response.text)

    def test_duplicate_poi_in_same_day_is_rejected_without_query_or_mutation(self):
        before = deepcopy(self.session)
        self.replace("bamboo-path", success=400)
        self.assertEqual(self.client.get(self.url).json(), before)
        self.map_mock.assert_not_called()

    def test_concurrent_change_during_route_query_does_not_overwrite_latest_state(self):
        self.refresh()
        version = self.session["version"]
        old_place = self.session["tripState"]["days"]["day2"]["stops"][0]["placeId"]
        def during_query(day, places, city, mode):
            saved = self.client.patch(self.url + "/direct", json={"baseVersion": version, "kind": "note", "targetRegionId": "day-2", "value": "另一窗口的新备注"})
            self.assertEqual(saved.status_code, 200, saved.text)
            return self.ready_route(day, places, city, mode)
        self.map_mock.side_effect = during_query
        self.replace(success=409)
        fresh = self.client.get(self.url).json()
        self.assertEqual(fresh["tripState"]["days"]["day2"]["note"], "另一窗口的新备注")
        self.assertEqual(fresh["tripState"]["days"]["day2"]["stops"][0]["placeId"], old_place)

    def test_all_day_refresh_and_undo_are_one_atomic_change(self):
        before = deepcopy(self.session)
        self.refresh("all")
        self.assertEqual(self.map_mock.call_count, 3)
        self.assertEqual(self.session["version"], before["version"] + 1)
        for region in module.REGIONS:
            self.assertTrue(self.session["uiSpec"]["regions"][region]["showMap"])
        self.request("/undo", {})
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.assertEqual(self.session["uiSpec"], before["uiSpec"])
        self.assertEqual(self.session["routeData"], before["routeData"])

    def test_catalog_migration_and_legacy_undo_do_not_restore_fictional_routes(self):
        current = module.store.get(self.session["id"])
        old_snapshot = module.payload_of(current)
        old_snapshot.pop("catalogVersion")
        old_snapshot["routeData"] = {"day-2": {"status": "ready", "points": [{"name": "虚构旧地点"}]}}
        current.pop("catalogVersion")
        current["routeData"] = deepcopy(old_snapshot["routeData"])
        current["_undo"] = [old_snapshot]
        current["version"] += 1
        module.store.save(current)
        self.session = self.client.get(self.url).json()
        self.assertEqual(self.session["routeData"], {})
        self.assertEqual(self.session["catalogVersion"], module.CATALOG_VERSION)
        trip = deepcopy(self.session["tripState"])
        self.request("/undo", {})
        self.assertEqual(self.session["routeData"], {})
        self.assertEqual(self.session["catalogVersion"], module.CATALOG_VERSION)
        self.assertEqual(self.session["tripState"], trip)

    def test_same_model_map_flags_can_refresh_without_display_changes(self):
        self.refresh()
        self.map_mock.reset_mock()
        self.settings = Settings(agent_mode="llm", llm_api_key="fixture-key")
        plan = agent.CandidatePlan(message="刷新第二天路线", scope="selected", operations=[agent.SetUI(kind="set_ui", regionId="day-2", showMap=True, showTransport=True)])
        scope = agent.EditScope(message="仅刷新第二天交通地图", permissions=[agent.RegionPermission(regionId="day-2", uiFields=["showMap", "showTransport"], requiredUI=agent.UIValues(showMap=True, showTransport=True))])
        with patch.object(agent, "scope_request", return_value=scope), patch.object(agent, "candidates_request", return_value=agent.CandidateBatch(candidates=[plan])):
            response = self.request("/generate", {"targetRegionId": "day-2", "instruction": "刷新第二天交通和地图"})
        self.assertEqual(response.json()["affectedRegionIds"], ["day-2"])
        self.map_mock.assert_called_once()

    def test_time_change_queries_visible_routes_with_new_time(self):
        self.refresh()
        self.map_mock.reset_mock()
        self.request("/direct", {"kind": "time", "targetRegionId": "day-2", "stopId": "day2-stop-1", "value": "10:45"}, "patch")
        self.map_mock.assert_called_once()
        self.assertEqual(self.map_mock.call_args.args[0]["stops"][0]["startTime"], "10:45")


if __name__ == "__main__":
    unittest.main()
