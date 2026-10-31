"""Open-ended Agent UI API regressions using only isolated mocked providers.

Run: python -m unittest backend.test_generated_api -v
"""
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

from fastapi.testclient import TestClient

from backend import access, agent
from backend.config import Settings

_import_directory = tempfile.TemporaryDirectory(prefix="travel-generated-api-import-")
atexit.register(_import_directory.cleanup)
with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": _import_directory.name}):
    module = importlib.import_module("backend.app")


class GeneratedAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="travel-generated-api-test-")
        self.addCleanup(self.directory.cleanup)
        self.previous_store = module.store
        module.store = module.Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", self.previous_store)
        settings = Settings(agent_mode="llm", llm_api_key="test-only-open-ui", requests_per_minute=120)
        for owner in (module, agent, access):
            patcher = patch.object(owner, "get_settings", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        with access._bucket_lock:
            access._buckets.clear()
        self.scope_value = {"message": "Only add the requested widget", "permissions": [{"regionId": "day-2", "editSurface": True}]}
        self.plans = [self.widget_plan()]
        self.candidate_calls = []
        self.candidate_callback = None
        self.scope_patch = patch.object(agent, "scope_request", side_effect=lambda *_args: agent.EditScope.model_validate(deepcopy(self.scope_value)))
        self.scope_mock = self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)
        candidates_patch = patch.object(agent, "candidates_request", side_effect=self.candidate_reply)
        self.candidates_mock = candidates_patch.start()
        self.addCleanup(candidates_patch.stop)
        guard = patch.object(agent, "_request_model", side_effect=AssertionError("API fixtures cannot contact real model services"))
        guard.start()
        self.addCleanup(guard.stop)
        maps_guard = patch.object(module, "enrich_day", side_effect=AssertionError("widget-only tests cannot query a real map"))
        maps_guard.start()
        self.addCleanup(maps_guard.stop)
        self.client = TestClient(module.app)
        self.addCleanup(self.client.close)
        self.session = self.client.post("/api/sessions").json()
        self.url = f"/api/sessions/{self.session['id']}"

    @staticmethod
    def widget_plan(label="Packing tool", javascript="document.querySelector('input').addEventListener('change', () => {});"):
        return {
            "title": label, "rationale": "Add a browser interaction without changing the trip.",
            "message": label, "scope": "selected", "operations": [{
                "kind": "set_surface", "regionId": "day-2", "surface": {
                    "title": label, "html": "<section><h2>Packing progress</h2><label><input type='checkbox'>Umbrella</label><output>0/1</output></section>",
                    "css": "section {padding: 12px; border: 1px solid #28614f;} output {display:block;}",
                    "javascript": javascript, "placement": "afterStops",
                },
            }],
        }

    def candidate_reply(self, *args, **kwargs):
        self.candidate_calls.append({"instruction": args[1], "target": args[2], "scope": args[4].model_dump(), "feedback": deepcopy(kwargs.get("feedback"))})
        if self.candidate_callback:
            plans = self.candidate_callback(len(self.candidate_calls))
        else:
            plans = self.plans
        return agent.CandidateBatch.model_validate({"message": "Prepared", "candidates": deepcopy(plans)})

    def generate(self, text="为第二天新增可勾选的行李清单和进度，不改其他内容"):
        return self.client.post(self.url + "/generate", json={"baseVersion": self.session["version"], "targetRegionId": "day-2", "instruction": text})

    def apply(self, proposal):
        response = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": proposal["proposalId"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return deepcopy(self.session)

    def current(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def repair(self, proposal, error="ReferenceError: packingCounter is not defined", **extra):
        body = {"baseVersion": self.session["version"], "error": error, **extra}
        return self.client.post(self.url + f"/proposals/{proposal['proposalId']}/repair", json=body)

    def test_widget_preview_apply_state_save_and_atomic_undo(self):
        before = deepcopy(self.session)
        response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        self.assertEqual(self.current(), before, "a preview cannot save generated code or mutate the trip")
        self.assertIn("<input", proposal["nextUI"]["surface"]["html"])
        applied = self.apply(proposal)
        self.assertEqual(applied["tripState"], before["tripState"])
        self.assertEqual(applied["uiSpec"]["regions"]["day-1"], before["uiSpec"]["regions"]["day-1"])
        self.assertEqual(applied["uiSpec"]["regions"]["day-3"], before["uiSpec"]["regions"]["day-3"])
        self.assertEqual(applied["uiSpec"]["regions"]["day-2"]["blocks"], before["uiSpec"]["regions"]["day-2"]["blocks"])
        saved = self.client.patch(self.url + "/direct", json={"baseVersion": applied["version"], "kind": "surface_state", "targetRegionId": "day-2", "value": {"packed": ["umbrella"], "search": "rain"}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.session = saved.json()
        self.assertEqual(self.session["uiSpec"]["regions"]["day-2"]["surfaceState"], {"packed": ["umbrella"], "search": "rain"})
        self.assertEqual(self.session["tripState"], before["tripState"])
        undone = self.client.post(self.url + "/undo", json={"baseVersion": self.session["version"]})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.session = undone.json()
        self.assertEqual(self.session["uiSpec"], applied["uiSpec"], "undo restores widget state together with code")
        undone = self.client.post(self.url + "/undo", json={"baseVersion": self.session["version"]})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.session = undone.json()
        self.assertEqual(self.session["uiSpec"], before["uiSpec"])
        self.assertEqual(self.session["tripState"], before["tripState"])

    def test_trip_only_budget_preserves_every_day_and_unrelated_global_fields(self):
        self.scope_value = {"message": "Only budget", "permissions": [], "tripFields": ["budgetLimit"]}
        self.plans = [{"title": "Budget", "message": "Budget 3200", "scope": "selected", "operations": [], "tripChanges": {"budgetLimit": 3200}}]
        before = deepcopy(self.session)
        response = self.generate("只把总预算调整为3200元，不修改任何日期的界面和行程")
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        self.assertEqual(proposal["updates"], [])
        self.assertEqual(proposal["affectedRegionIds"], [])
        self.assertEqual(self.current(), before)
        self.apply(proposal)
        self.assertEqual(self.session["tripState"]["budgetLimit"], 3200)
        for field in ("hotelId", "nights", "city", "days"):
            self.assertEqual(self.session["tripState"][field], before["tripState"][field])
        self.assertEqual(self.session["uiSpec"], before["uiSpec"])

    def test_trip_only_rejects_unrequested_hotel_and_keeps_saved_state(self):
        self.scope_value = {"message": "Only budget", "permissions": [], "tripFields": ["budgetLimit"]}
        other_hotel = next(key for key in module.HOTEL_BY_ID if key != self.session["tripState"]["hotelId"])
        self.plans = [{"title": "Out of scope", "message": "Wrong hotel", "scope": "selected", "operations": [], "tripChanges": {"budgetLimit": 3200, "hotelId": other_hotel}}]
        before = deepcopy(self.session)
        response = self.generate("只把预算调整为3200元，酒店和行程不变")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self.candidate_calls), 2, "one bounded retry is permitted, not a new scope")
        self.assertEqual(self.scope_mock.call_count, 1)
        self.assertEqual(self.current(), before)

    def test_real_catalog_hotel_change_is_supported_without_day_permissions(self):
        other_hotel = next(key for key in module.HOTEL_BY_ID if key != self.session["tripState"]["hotelId"])
        self.scope_value = {"message": "Only hotel", "permissions": [], "tripFields": ["hotelId"]}
        self.plans = [{"title": "Hotel", "message": "Change hotel", "scope": "selected", "operations": [], "tripChanges": {"hotelId": other_hotel}}]
        before = deepcopy(self.session)
        response = self.generate("只更换酒店，保持所有日期内容")
        self.assertEqual(response.status_code, 200, response.text)
        self.apply(response.json())
        self.assertEqual(self.session["tripState"]["hotelId"], other_hotel)
        self.assertEqual(self.session["tripState"]["days"], before["tripState"]["days"])
        self.assertEqual(self.session["uiSpec"], before["uiSpec"])

    def test_invalid_candidate_gets_execution_feedback_and_one_same_scope_repair(self):
        bad = {"title": "Wrong field", "message": "Wrong field", "scope": "selected", "operations": [{"kind": "set_ui", "regionId": "day-2", "showCost": False}]}
        self.candidate_callback = lambda call: [bad] if call == 1 else [self.widget_plan()]
        before = deepcopy(self.session)
        response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.candidate_calls), 2)
        self.assertIsNone(self.candidate_calls[0]["feedback"])
        self.assertTrue(self.candidate_calls[1]["feedback"])
        self.assertEqual(self.candidate_calls[0]["scope"], self.candidate_calls[1]["scope"])
        self.assertEqual(self.scope_mock.call_count, 1)
        self.assertEqual(self.current(), before)
        self.assertTrue(response.json()["nextUI"]["showCost"])

    def test_browser_repair_reuses_frozen_scope_original_instruction_and_candidate_source(self):
        instruction = "为第二天新增一个可勾选清单，不改原行程"
        response = self.generate(instruction)
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        frozen = deepcopy(self.candidate_calls[0]["scope"])
        self.scope_value = {"message": "Do not reparse this", "permissions": [{"regionId": "day-1", "editNote": True}]}
        self.plans = [self.widget_plan("Repaired", "document.querySelector('output').textContent = 'Ready';")]
        result = self.repair(proposal)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotEqual(result.json()["proposalId"], proposal["proposalId"])
        self.assertEqual(self.scope_mock.call_count, 1, "browser errors cannot request wider permissions")
        call = self.candidate_calls[-1]
        self.assertEqual(call["scope"], frozen)
        self.assertEqual(call["instruction"], instruction)
        self.assertIn("packingCounter", call["feedback"][0])
        self.assertIn(proposal["nextUI"]["surface"]["javascript"], call["feedback"][1])
        self.assertEqual(self.current(), self.session, "a repaired preview is still not applied")

    def test_browser_repair_is_owner_bound_and_session_bound(self):
        response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        calls = len(self.candidate_calls)
        with TestClient(module.app) as other:
            denied = other.post(self.url + f"/proposals/{proposal['proposalId']}/repair", json={"baseVersion": self.session["version"], "error": "runtime"})
            self.assertEqual(denied.status_code, 404, denied.text)
        other_session = self.client.post("/api/sessions").json()
        denied = self.client.post(f"/api/sessions/{other_session['id']}/proposals/{proposal['proposalId']}/repair", json={"baseVersion": other_session["version"], "error": "runtime"})
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertEqual(len(self.candidate_calls), calls)

    def test_browser_repair_rejects_old_proposal_after_state_changes(self):
        response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        previous_version = self.session["version"]
        direct = self.client.patch(self.url + "/direct", json={"baseVersion": previous_version, "kind": "note", "targetRegionId": "day-2", "value": "A newer note"})
        self.assertEqual(direct.status_code, 200, direct.text)
        self.session = direct.json()
        calls = len(self.candidate_calls)
        old_body = self.repair(proposal, baseVersion=previous_version)
        self.assertEqual(old_body.status_code, 409, old_body.text)
        new_body_old_proposal = self.repair(proposal)
        self.assertEqual(new_body_old_proposal.status_code, 409, new_body_old_proposal.text)
        self.assertEqual(len(self.candidate_calls), calls)
        self.assertEqual(self.current()["tripState"]["days"]["day2"]["note"], "A newer note")

    def test_browser_repair_chain_stops_after_two_repairs(self):
        response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        for number in range(2):
            self.plans = [self.widget_plan(f"Repair {number + 1}")]
            repaired = self.repair(proposal)
            self.assertEqual(repaired.status_code, 200, repaired.text)
            proposal = repaired.json()
        calls = len(self.candidate_calls)
        exhausted = self.repair(proposal)
        self.assertEqual(exhausted.status_code, 400, exhausted.text)
        self.assertEqual(len(self.candidate_calls), calls)

    def test_surface_state_requires_applied_surface_and_bounded_json_object(self):
        before = deepcopy(self.session)
        not_applied = self.client.patch(self.url + "/direct", json={"baseVersion": self.session["version"], "kind": "surface_state", "targetRegionId": "day-2", "value": {"packed": []}})
        self.assertEqual(not_applied.status_code, 400, not_applied.text)
        self.assertEqual(self.current(), before)
        generated = self.generate()
        self.assertEqual(generated.status_code, 200, generated.text)
        self.apply(generated.json())
        applied = deepcopy(self.session)
        for invalid in ([], "not an object", 7, True, None, {"large": "x" * 17000}):
            with self.subTest(kind=type(invalid).__name__):
                response = self.client.patch(self.url + "/direct", json={"baseVersion": self.session["version"], "kind": "surface_state", "targetRegionId": "day-2", "value": invalid})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.current(), applied)


    def test_scope_feedback_is_diagnostic_without_replacing_original_instruction(self):
        self.scope_patch.stop()
        instruction = "仅为第二天增加真实景点目录搜索，不改原行程"
        feedback = ["The requested local search is supported; do not reject unrequested live booking services."]
        expected = agent.EditScope(message="Only local catalog search", permissions=[agent.RegionPermission(regionId="day-2", editSurface=True)])
        with patch.object(agent, "_request_model", return_value=expected) as provider:
            result = agent.scope_request(self.session, instruction, "day-2", module.PLACES, feedback=feedback)
        self.assertEqual(result, expected)
        args = provider.call_args.args
        context = args[0]
        self.assertEqual(context["instruction"], instruction)
        self.assertEqual(context["selectedRegionId"], "day-2")
        self.assertEqual(context["validationFeedback"], feedback)
        self.assertEqual(args[1:], (agent.SCOPE_SYSTEM, agent.EditScope, "scope"))
        self.assertEqual(self.current(), self.session)

    def test_unrequested_service_boundary_warnings_do_not_block_catalog_search(self):
        scope_warning = "Search uses the verified local catalog; opening hours are not live."
        plan_warning = "No reservations or payment were requested or performed."
        self.scope_value = {"message": "Local catalog search is supported", "permissions": [{"regionId": "day-2", "editSurface": True}], "warnings": [scope_warning], "unsupportedCapabilities": [], "clarification": None}
        plan = self.widget_plan("Real catalog search")
        plan["warnings"] = [plan_warning]
        surface = plan["operations"][0]["surface"]
        surface["html"] = "<section><h2>Find a real place</h2><input id='query' aria-label='Search places'><ul id='matches'></ul></section>"
        surface["javascript"] = "const query=document.querySelector('#query'); const matches=document.querySelector('#matches'); function render(){matches.replaceChildren(); for(const place of window.travel.places.filter(p=>p.name.includes(query.value))){const item=document.createElement('li');item.textContent=place.name;matches.append(item);}} query.addEventListener('input',render);window.travel.onChange(render);"
        self.plans = [plan]
        before = deepcopy(self.session)
        response = self.generate("仅为第二天添加基于真实景点目录的本地搜索筛选，不需要实时票价和预订")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("query", response.json()["nextUI"]["surface"]["html"])
        self.assertEqual(self.scope_mock.call_count, 1)
        self.assertEqual(len(self.candidate_calls), 1)
        self.assertEqual(self.current(), before)

    def test_authorized_stop_removal_persists_stable_survivors_and_recalculates_costs(self):
        before = deepcopy(self.session)
        previous_day = before["tripState"]["days"]["day2"]
        remaining = deepcopy(previous_day["stops"][:-1])
        removed_place = module.PLACE_BY_ID[previous_day["stops"][-1]["placeId"]]
        self.scope_value = {"message": "Only remove the last second-day stop", "permissions": [{"regionId": "day-2", "editStructure": True}]}
        self.plans = [{"title": "Shorter day", "message": "Remove only the final stop", "scope": "selected", "operations": [{"kind": "set_day", "regionId": "day-2", "stops": remaining}]}]
        generated = self.generate("只删除第二天的最后一个景点，保留剩余景点、ID、时间、备注和其他内容")
        self.assertEqual(generated.status_code, 200, generated.text)
        self.assertEqual(self.current(), before, "structural previews must not save changes")
        self.apply(generated.json())
        saved = self.current()
        self.assertEqual(saved["tripState"]["days"]["day2"]["stops"], remaining)
        for field in ("date", "title", "note"):
            self.assertEqual(saved["tripState"]["days"]["day2"][field], previous_day[field])
        for key in ("day1", "day3"):
            self.assertEqual(saved["tripState"]["days"][key], before["tripState"]["days"][key])
        self.assertEqual(saved["uiSpec"], before["uiSpec"])
        self.assertEqual(saved["derived"]["attractions"], before["derived"]["attractions"] - removed_place["cost"])
        self.assertEqual(saved["derived"]["transport"], before["derived"]["transport"] - removed_place["transportCost"])
        self.assertEqual(saved["derived"]["hotel"], before["derived"]["hotel"])
        self.assertEqual(saved["derived"]["food"], before["derived"]["food"])
        self.assertEqual(saved["derived"]["total"], before["derived"]["total"] - removed_place["cost"] - removed_place["transportCost"])
        self.assertEqual(saved["derived"]["remaining"], saved["tripState"]["budgetLimit"] - saved["derived"]["total"])
        stored = module.store.get(saved["id"])
        self.assertEqual(stored["tripState"], saved["tripState"], "the saved database document contains the structural change")

    def test_authorized_date_change_refreshes_existing_visible_route_dependency(self):
        def route_fixture(day, places, city, mode):
            return {
                "status": "ready", "provider": "amap", "message": f"Isolated fixture for {day['date']}",
                "queriedAt": day["date"] + "T00:00:00Z", "points": [{
                    "stopId": stop["id"], "name": places[stop["placeId"]]["name"],
                    "status": "resolved", "lng": 120.1 + index / 100, "lat": 30.2 + index / 100,
                    "searchUrl": "https://uri.amap.com/search",
                } for index, stop in enumerate(day["stops"])], "segments": [],
            }
        with patch.object(module, "enrich_day", side_effect=route_fixture) as routes:
            connected = self.client.post(self.url + "/routes", json={"baseVersion": self.session["version"], "targetRegionId": "day-2"})
            self.assertEqual(connected.status_code, 200, connected.text)
            self.session = connected.json()
            before = deepcopy(self.session)
            self.assertTrue(before["uiSpec"]["regions"]["day-2"]["showMap"])
            previous_day = before["tripState"]["days"]["day2"]
            new_date = (date.fromisoformat(previous_day["date"]) + timedelta(days=7)).isoformat()
            self.scope_value = {"message": "Only move the second-day date", "permissions": [{"regionId": "day-2", "editDate": True}]}
            self.plans = [{"title": "Later date", "message": "Change only the second-day date", "scope": "selected", "operations": [{"kind": "set_day", "regionId": "day-2", "date": new_date}]}]
            generated = self.generate(f"只把第二天日期改为{new_date}，保留景点、时间、备注和界面")
            self.assertEqual(generated.status_code, 200, generated.text)
            self.assertEqual(self.current(), before, "route querying during a proposal cannot commit it")
            proposal = generated.json()
            self.assertEqual(routes.call_count, 2, "initial route load plus exactly one query for the changed date")
            self.assertEqual(routes.call_args.args[0]["date"], new_date)
            self.assertEqual(proposal["updates"][0]["routeData"]["message"], f"Isolated fixture for {new_date}")
            self.apply(proposal)
            self.assertEqual(routes.call_count, 2, "applying reuses the validated proposal route")
        saved = self.current()
        self.assertEqual(saved["tripState"]["days"]["day2"]["date"], new_date)
        self.assertEqual(saved["tripState"]["days"]["day2"]["stops"], previous_day["stops"])
        self.assertEqual(saved["routeData"]["day-2"]["message"], f"Isolated fixture for {new_date}")
        self.assertNotEqual(saved["routeData"]["day-2"], before["routeData"]["day-2"])
        self.assertEqual(saved["uiSpec"], before["uiSpec"])
        self.assertEqual(saved["derived"], before["derived"])
        for key in ("day1", "day3"):
            self.assertEqual(saved["tripState"]["days"][key], before["tripState"]["days"][key])

    def test_structure_permission_cannot_change_other_fields_but_explicit_globals_commit(self):
        before = deepcopy(self.session)
        day = before["tripState"]["days"]["day2"]
        remaining = deepcopy(day["stops"][:-1])
        new_date = (date.fromisoformat(day["date"]) + timedelta(days=7)).isoformat()
        other_hotel = next(key for key in module.HOTEL_BY_ID if key != before["tripState"]["hotelId"])
        changed_times = deepcopy(remaining)
        changed_times[0]["startTime"] = "11:45"
        variants = [
            ({"title": "Unauthorized title"}, None),
            ({"date": new_date}, None),
            ({"stops": changed_times}, None),
            ({}, {"hotelId": other_hotel}),
            ({}, {"budgetLimit": 3500}),
            ({}, {"nights": 3}),
        ]
        for extra_day_fields, trip_changes in variants:
            with self.subTest(fields=extra_day_fields, trip=trip_changes):
                self.scope_value = {"message": "Only remove the last stop", "permissions": [{"regionId": "day-2", "editStructure": True}]}
                operation = {"kind": "set_day", "regionId": "day-2", "stops": deepcopy(remaining), **extra_day_fields}
                plan = {"title": "Exceeds permission", "message": "Must be rejected", "scope": "selected", "operations": [operation]}
                if trip_changes:
                    plan["tripChanges"] = trip_changes
                self.plans = [plan]
                response = self.generate("只删除第二天最后一个景点，其他内容都不改")
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.current(), before, "unauthorized fields cannot be saved by a structural operation")
        self.scope_value = {"message": "Explicit hotel, budget and nights update", "permissions": [], "tripFields": ["hotelId", "budgetLimit", "nights"]}
        self.plans = [{"title": "Global trip settings", "message": "Apply requested global settings", "scope": "selected", "operations": [], "tripChanges": {"hotelId": other_hotel, "budgetLimit": 3500, "nights": 3}}]
        generated = self.generate("只更换酒店、将总预算设为3500元并住宿3晚，不修改日期内容和界面")
        self.assertEqual(generated.status_code, 200, generated.text)
        self.apply(generated.json())
        saved = self.current()
        self.assertEqual(saved["tripState"]["hotelId"], other_hotel)
        self.assertEqual(saved["tripState"]["budgetLimit"], 3500)
        self.assertEqual(saved["tripState"]["nights"], 3)
        self.assertEqual(saved["tripState"]["days"], before["tripState"]["days"])
        self.assertEqual(saved["uiSpec"], before["uiSpec"])
        self.assertEqual(saved["derived"]["hotel"], module.HOTEL_BY_ID[other_hotel]["nightlyPrice"] * 3)
        self.assertEqual(saved["derived"]["total"], before["derived"]["total"] - before["derived"]["hotel"] + saved["derived"]["hotel"])
        self.assertEqual(saved["derived"]["remaining"], 3500 - saved["derived"]["total"])

if __name__ == "__main__":
    unittest.main()
