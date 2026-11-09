"""Personalized trip API regressions with isolated storage and guarded providers."""
from __future__ import annotations

import atexit
from copy import deepcopy
from datetime import date, timedelta
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import access, agent, maps, trip_setup
from backend.config import Settings

_import_directory = tempfile.TemporaryDirectory(prefix="travel-personalized-api-import-")
atexit.register(_import_directory.cleanup)
with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": _import_directory.name}):
    module = importlib.import_module("backend.app")


class PersonalizedAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="travel-personalized-api-test-")
        self.addCleanup(self.directory.cleanup)
        previous_store = module.store
        module.store = module.Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", previous_store)
        settings = Settings(agent_mode="llm", llm_api_key="fixture-only-personalized", requests_per_minute=120)
        for owner in (module, agent, access, maps, trip_setup):
            patcher = patch.object(owner, "get_settings", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        with access._bucket_lock:
            access._buckets.clear()
        builder = patch.object(module, "build_trip_payload", side_effect=self.payload)
        self.builder = builder.start()
        self.addCleanup(builder.stop)
        self.provider_guards = []
        for owner, name in ((agent, "_request_model"), (agent, "scope_request"), (agent, "candidates_request"), (maps, "_request"), (module, "enrich_day")):
            guard = patch.object(owner, name, side_effect=AssertionError("Personalized API tests cannot call an unmocked provider"))
            self.provider_guards.append(guard.start())
            self.addCleanup(guard.stop)
        self.client = TestClient(module.app)
        self.addCleanup(self.client.close)
        self.setup_body = {"city": "苏州", "dayCount": 14, "budgetLimit": 9000, "startDate": "2026-11-01", "travelers": 3, "preferences": "慢节奏"}
        response = self.client.post("/api/sessions", json=self.setup_body)
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.initial = deepcopy(self.session)
        self.url = f"/api/sessions/{self.session['id']}"

    @staticmethod
    def payload(body):
        # Fixture identities are intentionally separate from the Hangzhou catalog.
        places = [{"id": f"fixture-suzhou-{i}", "name": f"苏州测试景点{i}", "city": "苏州市", "address": f"苏州测试路{i}号", "location": "苏州测试区", "indoor": bool(i % 2), "category": "测试地点", "description": "Isolated test fixture", "durationMinutes": 60, "cost": i, "transportCost": 2, "costEstimated": True, "mapPoiId": f"BTESTPLACE{i:04}", "sourceUrl": f"https://www.amap.com/place/BTESTPLACE{i:04}"} for i in range(1, 17)]
        hotels = [{"id": f"fixture-suzhou-hotel-{i}", "name": f"苏州测试酒店{i}", "address": f"苏州测试酒店路{i}号", "location": "苏州测试区", "description": "Isolated hotel fixture", "nightlyPrice": 200 * i, "priceEstimated": True, "sourceUrl": f"https://www.amap.com/place/BTESTHOTEL{i:04}"} for i in range(1, 3)]
        catalog = {"city": "苏州市", "adcode": "320500", "version": "fixture-suzhou-v1", "places": places, "hotels": hotels, "pricing": deepcopy(trip_setup.PRICING_POLICY)}
        days, regions = {}, {}
        start = date.fromisoformat(body.startDate)
        for number in range(1, body.dayCount + 1):
            key, region = f"day{number}", f"day-{number}"
            days[key] = {"title": f"第{number}天独有行程", "date": (start + timedelta(days=number - 1)).isoformat(), "note": f"独有备注{number}", "stops": [{"id": f"{key}-stop-1", "placeId": places[number - 1]["id"], "startTime": "09:30"}]}
            regions[region] = {"id": region, "binding": f"days.{key}", "view": "cards", "showCost": True, "showAlternatives": False, "compact": False, "showTransport": False, "showMap": False, "transportMode": "walking", "mapPlacement": "afterStops", "transportStyle": "inline", "blocks": [], "surface": None, "surfaceState": {"checked": [f"own-{number}"]}}
        regions["day-1"].update(view="timeline", compact=True, surface={"title": "城市漫游面板", "html": "<section><h2></h2><p>当前行程</p></section>", "css": "section{padding:12px;color:#365843}", "javascript": "window.travel.onChange(()=>{document.querySelector('h2').textContent=window.travel.day.title;});", "placement": "afterStops"})
        if body.dayCount > 1:
            regions["day-2"]["blocks"] = [{"id": "own-note-2", "type": "note", "title": "个人提醒", "text": "保留本日已有内容", "items": [], "columns": [], "rows": []}]
        rooms = (body.travelers + 1) // 2
        return {"tripState": {"city": "苏州市", "budgetLimit": body.budgetLimit, "hotelId": hotels[0]["id"], "nights": body.dayCount - 1, "travelers": body.travelers, "roomCount": rooms, "days": days}, "uiSpec": {"regions": regions}, "controlState": {"lockedRegions": [], "hotelLocked": False}, "routeData": {}, "catalogVersion": catalog["version"], "catalog": catalog, "planningSettings": {**body.model_dump(), "city": "苏州市", "roomCount": rooms, "warnings": ["Isolated fixture, not a real-model or map trial"]}}

    def current(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def mutate(self, suffix, body=None, method="post"):
        response = getattr(self.client, method)(self.url + suffix, json={"baseVersion": self.session["version"], **(body or {})})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session

    def transfer(self, targets=None):
        return self.client.post(self.url + "/copy-style", json={"baseVersion": self.session["version"], "sourceRegionId": "day-1", "targetRegionIds": targets or ["day-2", "day-3"]})

    def apply(self, batch):
        return self.mutate("/apply", {"proposalId": batch["candidates"][0]["proposalId"]})

    def test_create_persists_session_catalog_fourteen_days_and_traveler_costs(self):
        value = self.current()
        self.assertEqual(self.builder.call_args.args[0].model_dump(), {**self.setup_body, "budgetLimit": 9000.0})
        self.assertEqual(value["tripState"]["city"], "苏州市")
        self.assertEqual(len(value["tripState"]["days"]), 14)
        self.assertEqual(set(value["uiSpec"]["regions"]), {f"day-{i}" for i in range(1, 15)})
        self.assertEqual(value["tripState"]["days"]["day14"]["date"], "2026-11-14")
        self.assertEqual(value["catalog"]["pricing"]["foodPerPersonDay"], 180)
        self.assertEqual(value["derived"], {"attractions": 315, "transport": 84, "food": 7560, "hotel": 5200, "total": 13159, "remaining": -4159.0})
        self.assertEqual(value["catalog"]["places"][0]["id"], "fixture-suzhou-1")
        self.assertEqual(value["catalog"]["hotels"][0]["id"], "fixture-suzhou-hotel-1")
        stored = module.store.get(value["id"])
        self.assertEqual(stored["catalog"], value["catalog"])
        self.assertEqual(stored["_initialPayload"]["tripState"], value["tripState"])
        self.assertEqual(stored["planningSettings"]["preferences"], "慢节奏")

    def test_day_fourteen_direct_generation_undo_and_reset_keep_personalized_trip(self):
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-14", "value": "最后一天用户备注"}, "patch")
        self.mutate("/direct", {"kind": "time", "targetRegionId": "day-14", "stopId": "day14-stop-1", "value": "11:20"}, "patch")
        self.mutate("/direct", {"kind": "replace_stop", "targetRegionId": "day-14", "stopId": "day14-stop-1", "value": "fixture-suzhou-15"}, "patch")
        self.mutate("/direct", {"kind": "budget", "value": 12345}, "patch")
        before = deepcopy(self.current())
        scope = agent.EditScope(message="只改最后一天备注", permissions=[agent.RegionPermission(regionId="day-14", editNote=True)])
        plan = agent.CandidateBatch(candidates=[agent.CandidatePlan(title="最后一天备注", message="更新备注", scope="selected", operations=[agent.SetNote(kind="set_note", regionId="day-14", note="Agent第十四天备注")])])
        with patch.object(agent, "scope_request", return_value=scope), patch.object(agent, "candidates_request", return_value=plan):
            response = self.client.post(self.url + "/generate", json={"baseVersion": self.session["version"], "targetRegionId": "day-14", "instruction": "只修改第十四天备注，保留其他日期", "generateCandidates": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.current(), before, "a generated preview cannot save a dynamic-date edit")
        self.apply(response.json())
        self.assertEqual(self.current()["tripState"]["days"]["day14"]["note"], "Agent第十四天备注")
        self.mutate("/undo")
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.mutate("/reset")
        for key in ("tripState", "uiSpec", "catalog", "planningSettings"):
            self.assertEqual(self.session[key], self.initial[key], key)
        self.assertEqual(self.session["tripState"]["city"], "苏州市")
        invalid = self.client.patch(self.url + "/direct", json={"baseVersion": self.session["version"], "kind": "replace_stop", "targetRegionId": "day-14", "stopId": "day14-stop-1", "value": "west-lake"})
        self.assertEqual(invalid.status_code, 400, invalid.text)

    def test_copy_style_preview_apply_and_undo_preserve_source_data_and_target_state(self):
        before = deepcopy(self.current())
        response = self.transfer()
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()
        self.assertEqual(batch["affectedRegionIds"], ["day-2", "day-3"])
        self.assertEqual(self.current(), before)
        self.assertEqual(batch["candidateCount"], 1)
        for update in batch["updates"]:
            region = update["regionId"]
            self.assertNotIn("nextData", update)
            self.assertEqual(update["nextUI"]["id"], region)
            self.assertEqual(update["nextUI"]["binding"], "days.day" + region.split("-")[1])
            self.assertEqual(update["nextUI"]["view"], "timeline")
            self.assertEqual(update["nextUI"]["surfaceState"], before["uiSpec"]["regions"][region]["surfaceState"])
            self.assertEqual(update["nextUI"]["blocks"], before["uiSpec"]["regions"][region]["blocks"])
        self.apply(batch)
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.assertEqual(self.session["uiSpec"]["regions"]["day-1"], before["uiSpec"]["regions"]["day-1"])
        for region in ("day-2", "day-3"):
            self.assertEqual(self.session["uiSpec"]["regions"][region]["view"], "timeline")
            self.assertEqual(self.session["uiSpec"]["regions"][region]["surfaceState"], before["uiSpec"]["regions"][region]["surfaceState"])
        self.mutate("/undo")
        for key in ("tripState", "uiSpec", "catalog", "routeData"):
            self.assertEqual(self.session[key], before[key])

    def test_natural_style_transfer_uses_read_only_source_and_explicit_destinations(self):
        self.mutate("/locks", {"target": "day-1", "locked": True}, "patch")
        before = deepcopy(self.current())
        response = self.client.post(self.url + "/generate", json={"baseVersion": self.session["version"], "targetRegionId": "day-1", "instruction": "我想将单独修改后第一天的页面应用于第二天和第三天", "generateCandidates": False})
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()
        self.assertEqual(batch["affectedRegionIds"], ["day-2", "day-3"])
        self.assertEqual(self.current(), before)
        self.apply(batch)
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.assertEqual(self.session["uiSpec"]["regions"]["day-1"], before["uiSpec"]["regions"]["day-1"])
        self.assertIn("day-1", self.session["controlState"]["lockedRegions"])

    def test_transfer_respects_target_locks_versions_and_visitor_ownership(self):
        self.mutate("/locks", {"target": "day-3", "locked": True}, "patch")
        locked = deepcopy(self.current())
        response = self.transfer()
        self.assertEqual(response.status_code, 423, response.text)
        self.assertEqual(self.current(), locked)
        self.mutate("/locks", {"target": "day-3", "locked": False}, "patch")
        response = self.transfer()
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()
        with TestClient(module.app) as other:
            denied = other.post(self.url + "/copy-style", json={"baseVersion": self.session["version"], "sourceRegionId": "day-1", "targetRegionIds": ["day-2"]})
            self.assertEqual(denied.status_code, 404, denied.text)
            denied_apply = other.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": batch["proposalId"]})
            self.assertEqual(denied_apply.status_code, 404, denied_apply.text)
        old_version = self.session["version"]
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "方案之后的新修改"}, "patch")
        stale = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": batch["proposalId"]})
        self.assertEqual(stale.status_code, 409, stale.text)
        stale_copy = self.client.post(self.url + "/copy-style", json={"baseVersion": old_version, "sourceRegionId": "day-1", "targetRegionIds": ["day-2"]})
        self.assertEqual(stale_copy.status_code, 409, stale_copy.text)
        self.assertEqual(self.current()["tripState"]["days"]["day2"]["note"], "方案之后的新修改")

    def test_failed_new_planning_and_invalid_setup_leave_existing_session_intact(self):
        before = deepcopy(self.current())
        self.builder.side_effect = HTTPException(502, "Isolated city lookup failure")
        failed = self.client.post("/api/sessions", json={**self.setup_body, "city": "成都", "dayCount": 4})
        self.assertEqual(failed.status_code, 502, failed.text)
        invalid = self.client.post("/api/sessions", json={"city": "成都"})
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(self.current(), before)
        self.assertEqual(module.store.get(self.session["id"])["tripState"], before["tripState"])

    def test_routes_and_replacement_use_current_city_catalog_on_day_fourteen(self):
        def fixture_route(day, places, city, mode):
            self.assertEqual(city, "苏州市")
            self.assertEqual(day["date"], "2026-11-14")
            self.assertTrue(all(key.startswith("fixture-suzhou-") for key in places))
            return {"status": "ready", "provider": "amap", "message": "Isolated route fixture", "queriedAt": "2026-10-29T00:00:00Z", "points": [{"stopId": stop["id"], "name": places[stop["placeId"]]["name"], "status": "resolved", "lng": 120.6, "lat": 31.3, "searchUrl": "https://uri.amap.com/search"} for stop in day["stops"]], "segments": []}
        with patch.object(module, "enrich_day", side_effect=fixture_route) as provider:
            self.mutate("/routes", {"targetRegionId": "day-14"})
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(self.session["routeData"]["day-14"]["points"][0]["name"], "苏州测试景点14")
            self.mutate("/direct", {"kind": "replace_stop", "targetRegionId": "day-14", "stopId": "day14-stop-1", "value": "fixture-suzhou-15"}, "patch")
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(self.session["routeData"]["day-14"]["points"][0]["name"], "苏州测试景点15")
        self.assertEqual(self.session["tripState"]["days"]["day1"], self.initial["tripState"]["days"]["day1"])


if __name__ == "__main__":
    unittest.main()
