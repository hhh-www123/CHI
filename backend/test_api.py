"""Critical mutation invariants: python -m unittest backend.test_api -v."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import ValidationError

from backend import access
from backend import app as module
from backend.config import Settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_store = module.store
        module.store = module.Store(Path(self.temporary.name))
        self.settings = Settings(agent_mode="demo", requests_per_minute=120)
        self.module_settings = patch.object(module, "get_settings", return_value=self.settings)
        self.access_settings = patch.object(access, "get_settings", return_value=self.settings)
        self.module_settings.start()
        self.access_settings.start()
        with access._bucket_lock:
            access._buckets.clear()
        self.client = TestClient(module.app)
        response = self.client.post("/api/sessions")
        self.assertEqual(response.status_code, 200)
        self.session = response.json()
        self.url = f"/api/sessions/{self.session['id']}"

    def tearDown(self):
        self.client.close()
        self.access_settings.stop()
        self.module_settings.stop()
        module.store = self.previous_store
        self.temporary.cleanup()

    def mutate(self, endpoint, payload, method="post"):
        payload = {"baseVersion": self.session["version"], **payload}
        response = getattr(self.client, method)(self.url + endpoint, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session

    def preview(self, instruction="改成时间轴", target="day-2"):
        return self.client.post(self.url + "/generate", json={"baseVersion": self.session["version"], "targetRegionId": target, "instruction": instruction})

    def test_preview_apply_changes_only_selected_ui_and_preserves_notes(self):
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "下午记得休息"}, "patch")
        before = deepcopy(self.session)
        response = self.preview("从卡片改成时间轴")
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        self.assertEqual(self.client.get(self.url).json(), before, "预览不能改变会话")
        self.mutate("/apply", {"proposalId": proposal["proposalId"]})
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.assertEqual(self.session["uiSpec"]["regions"]["day-1"], before["uiSpec"]["regions"]["day-1"])
        self.assertEqual(self.session["uiSpec"]["regions"]["day-2"]["view"], "timeline")

    def test_rain_preserves_stop_ids_times_notes_and_other_days(self):
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "已订午餐"}, "patch")
        before = deepcopy(self.session)
        proposal = self.preview("改成雨天方案").json()
        self.mutate("/apply", {"proposalId": proposal["proposalId"]})
        day = self.session["tripState"]["days"]["day2"]
        self.assertEqual(day["note"], "已订午餐")
        for old, new in zip(before["tripState"]["days"]["day2"]["stops"], day["stops"]):
            self.assertEqual(old["id"], new["id"])
            self.assertEqual(old["startTime"], new["startTime"])
            self.assertTrue(module.PLACE_BY_ID[new["placeId"]]["indoor"])
        self.assertEqual(before["tripState"]["days"]["day1"], self.session["tripState"]["days"]["day1"])
        self.assertEqual(before["tripState"]["hotelId"], self.session["tripState"]["hotelId"])

    def test_locks_stale_preview_and_stale_writes(self):
        proposal = self.preview().json()
        old_version = self.session["version"]
        self.mutate("/locks", {"target": "day-2", "locked": True}, "patch")
        self.assertEqual(self.preview().status_code, 423)
        stale = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": proposal["proposalId"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.post(self.url + "/reset", json={"baseVersion": old_version}).status_code, 409)
        # Locks protect generated edits while direct user input stays available.
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "用户直接填写"}, "patch")
        self.mutate("/locks", {"target": "hotel", "locked": True}, "patch")
        change_hotel = self.client.patch(self.url + "/direct", json={"baseVersion": self.session["version"], "kind": "hotel", "value": "canal-hotel"})
        self.assertEqual(change_hotel.status_code, 423)

    def test_unknown_instruction_and_invalid_inputs_do_not_mutate(self):
        before = deepcopy(self.session)
        response = self.preview("帮我预订机票并付款")
        self.assertEqual(response.status_code, 400)
        invalid_budget = self.client.patch(self.url + "/direct", json={"baseVersion": self.session["version"], "kind": "budget", "value": True})
        self.assertEqual(invalid_budget.status_code, 400)
        invalid_time = self.client.patch(self.url + "/direct", json={"baseVersion": self.session["version"], "kind": "time", "targetRegionId": "day-2", "stopId": "day2-stop-1", "value": "28:99"})
        self.assertEqual(invalid_time.status_code, 400)
        self.assertEqual(self.client.get(self.url).json(), before)

    def test_undo_versions_audit_and_restart_persistence(self):
        before = deepcopy(self.session)
        self.mutate("/direct", {"kind": "budget", "value": 1200}, "patch")
        self.assertEqual(self.session["derived"]["remaining"], 1200 - self.session["derived"]["total"])
        changed_version = self.session["version"]
        self.mutate("/undo", {})
        self.assertGreater(self.session["version"], changed_version)
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.assertEqual(len(self.session["history"]), 3)
        module.store = module.Store(Path(self.temporary.name))
        self.assertEqual(self.client.get(self.url).json(), self.session)

    def test_schema_rejects_unknown_view_and_cross_region_binding(self):
        region = deepcopy(self.session["uiSpec"]["regions"]["day-2"])
        region["view"] = "arbitrary-html"
        with self.assertRaises(ValidationError):
            module.UI_VALIDATOR.validate(region)
        region["view"] = "cards"
        region["binding"] = "days.day1"
        with self.assertRaises(module.HTTPException):
            module.validate_region(region, "day-2")

    def test_independent_commands_do_not_mix_negation_or_cross_sessions(self):
        response = self.preview("改成时间轴，隐藏费用，增加室内备选")
        self.assertEqual(response.status_code, 200)
        proposal = response.json()
        self.assertFalse(proposal["nextUI"]["showCost"])
        self.assertTrue(proposal["nextUI"]["showAlternatives"])
        other = self.client.post("/api/sessions").json()
        cross_session = self.client.post(f"/api/sessions/{other['id']}/apply", json={"baseVersion": other["version"], "proposalId": proposal["proposalId"]})
        self.assertEqual(cross_session.status_code, 404)
        self.mutate("/direct", {"kind": "budget", "value": 3000}, "patch")
        stale_apply = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": proposal["proposalId"]})
        self.assertEqual(stale_apply.status_code, 409)


if __name__ == "__main__":
    unittest.main()
