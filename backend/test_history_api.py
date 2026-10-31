"""Phase 3 immutable history, explicit restore/constraints and atomicity tests.

Every test uses an isolated SQLite directory and mocked service settings; no live
model, map, official trip database or personal browser profile is used.
"""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import access, agent, maps, candidate_api, candidate_agent
from backend.config import Settings
from backend.persistence import Store
from backend.versioning import payload_snapshot

with tempfile.TemporaryDirectory(prefix="phase3-import-") as import_dir:
    with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": import_dir}):
        module = importlib.import_module("backend.app")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="phase3-history-")
        self.addCleanup(self.directory.cleanup)
        previous = module.store
        module.store = Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", previous)
        settings = Settings(agent_mode="demo", candidate_agent_mode="mock", llm_api_key="", amap_key="", requests_per_minute=120)
        for owner in (module, access, agent, maps, candidate_api, candidate_agent):
            handle = patch.object(owner, "get_settings", return_value=settings)
            handle.start(); self.addCleanup(handle.stop)
        with access._bucket_lock:
            access._buckets.clear()
        handle = patch.object(maps, "_request_once", side_effect=AssertionError("Live maps forbidden"))
        handle.start(); self.addCleanup(handle.stop)
        self.client = TestClient(module.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.session = self.client.post("/api/sessions", json={}).json()
        self.url = "/api/sessions/" + self.session["id"]
        self.pm = self.binding("PeriodGroup", "day2", "pm")
        self.day2 = self.binding("DayCard", "day2")

    def binding(self, kind, day, period=None):
        return next(item for item in self.session["componentRegistry"].values() if item["kind"] == kind and item["dayKey"] == day and (period is None or item.get("period") == period))

    def rows(self):
        return module.store.versions(self.session["id"])

    def stored(self):
        return module.store.get(self.session["id"])

    def accepted(self, endpoint, data, method="post"):
        response = getattr(self.client, method)(self.url + endpoint, json={"baseVersion": self.session["version"], **data})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session

    def denied(self, endpoint, data, expected=400, method="post"):
        before, rows = self.stored(), self.rows()
        response = getattr(self.client, method)(self.url + endpoint, json={"baseVersion": self.session["version"], **data})
        self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.rows(), rows)
        return response

    def batch(self):
        response = self.client.post(self.url + "/edits/candidates", json={"baseVersion": self.session["version"], "selectedComponentIds": [self.pm["componentId"]], "instruction": "安排轻松一些", "agentMode": "mock"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def cap(self, value=3000):
        return self.accepted("/editor/constraints", {"confirmConstraintChange": True, "maxBudget": value})

    def test_initial_record_safe_complete_and_reload_is_read_only(self):
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        record = self.client.get(self.url + "/versions/1").json()
        required = {"tripId", "version", "parentVersion", "baseVersion", "userRequest", "selectedComponentIds", "candidateId", "patch", "documentSnapshot", "payloadSnapshot", "constraintsSnapshot", "changedPaths", "validationReport"}
        self.assertTrue(required <= record.keys())
        self.assertNotIn("_owner", json.dumps(record))
        self.assertNotIn("_undo", json.dumps(record))
        self.assertEqual(record["documentSnapshot"], self.session["tripDocument"])
        self.assertEqual(self.client.get(self.url + "/versions").json()["currentVersion"], 1)
        self.assertEqual(self.rows(), rows)

    def test_candidate_commit_records_exact_provenance_and_preview_writes_nothing(self):
        before = self.stored(); rows = self.rows(); batch = self.batch()
        self.assertEqual(self.stored(), before); self.assertEqual(self.rows(), rows)
        chosen = next(item for item in batch["candidates"] if item["strategy"] == "efficiency_first")
        self.accepted("/edits/commit", {"candidateId": chosen["candidateId"]})
        record = self.rows()[0]
        self.assertEqual(record["candidateId"], chosen["candidateId"])
        self.assertEqual(record["patch"], chosen["patch"])
        self.assertEqual(record["parentVersion"], 1)
        self.assertEqual(record["baseVersion"], 1)
        self.assertEqual(record["userRequest"], "安排轻松一些")
        self.assertEqual(record["selectedComponentIds"], [self.pm["componentId"]])
        self.assertEqual(record["documentSnapshot"], self.session["tripDocument"])
        self.assertEqual(record["constraintsSnapshot"], self.session["persistedConstraints"])
        self.assertTrue(record["validationReport"]["valid"])
        self.assertTrue(all(path.endswith("/startTime") for path in record["payloadChangedPaths"]))

    def test_failed_record_insert_rolls_back_current_state_and_history(self):
        with module.store._connect() as connection:
            connection.execute("CREATE TRIGGER fail_version BEFORE INSERT ON trip_versions WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT,'test injected failure'); END")
        self.denied("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "must rollback"}, 503, "patch")

    def test_stale_store_cas_cannot_leave_an_extra_record(self):
        second = Store(Path(self.directory.name))
        stale = second.get(self.session["id"])
        self.accepted("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "winner"}, "patch")
        stale["version"] += 1; stale["tripState"]["days"]["day2"]["note"] = "loser"
        with self.assertRaises(HTTPException) as error:
            second.save(stale)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[0]["payloadSnapshot"]["tripState"]["days"]["day2"]["note"], "winner")

    def test_old_database_gets_only_current_checkpoint_not_fabricated_history(self):
        old = self.stored(); old["version"] = 7
        with module.store._connect() as connection:
            connection.execute("DELETE FROM trip_versions")
            connection.execute("UPDATE sessions SET version=7,document=? WHERE id=?", (json.dumps(old), old["id"]))
        response = self.client.get(self.url + "/versions")
        self.assertEqual(response.status_code, 200, response.text)
        entries = response.json()["versions"]
        self.assertEqual([r["version"] for r in entries], [7])
        self.assertEqual(entries[0]["kind"], "checkpoint")
        self.assertIsNone(entries[0]["parentVersion"])
        self.assertFalse(entries[0]["validationReport"]["historicalValidationReplayed"])
        self.assertEqual(self.client.get(self.url + "/versions/1").status_code, 404)

    def test_budget_hard_cap_is_versioned_and_direct_edit_cannot_exceed_it(self):
        self.cap()
        old = deepcopy(self.session["persistedConstraints"])
        self.denied("/direct", {"kind": "budget", "value": 3100}, 400, "patch")
        self.accepted("/direct", {"kind": "budget", "value": 2900}, "patch")
        self.assertEqual(self.session["persistedConstraints"], old)
        self.assertTrue(any(c["kind"] == "max_budget" for c in self.rows()[0]["constraintsSnapshot"]["hardConstraints"]))

    def test_direct_time_cannot_break_historical_time_order(self):
        self.denied("/direct", {"kind": "time", "targetRegionId": "day-2", "stopId": "day2-stop-1", "value": "17:00"}, 400, "patch")

    def test_ordinary_commit_cannot_remove_persistent_or_legacy_locks(self):
        locked = self.binding("DayCard", "day1")
        self.accepted("/editor/locks", {"componentId": locked["componentId"], "locked": True}, "patch")
        for key in ("persistedConstraints", "controlState"):
            before = self.stored(); candidate = module.payload_of(before)
            if key == "persistedConstraints": candidate[key]["lockedComponentIds"] = []
            else: candidate[key]["hotelLocked"] = True
            with self.assertRaises(HTTPException) as error:
                module.commit(before, candidate, "forged ordinary edit", "direct")
            self.assertEqual(error.exception.status_code, 423)
            self.assertEqual(self.stored(), before)

    def test_local_style_requires_day_selection_and_respects_locks(self):
        self.denied("/editor/display", {"selectedComponentId": self.pm["componentId"], "compact": True}, 403)
        day1 = self.binding("DayCard", "day1")
        self.accepted("/editor/locks", {"componentId": day1["componentId"], "locked": True}, "patch")
        self.denied("/editor/display", {"selectedComponentId": day1["componentId"], "view": "timeline"}, 423)
        self.accepted("/editor/display", {"selectedComponentId": self.day2["componentId"], "compact": True})
        record = self.rows()[0]
        self.assertEqual(record["allowedPaths"], ["/uiSpec/regions/day-2/compact"])
        self.assertEqual(record["payloadChangedPaths"], record["allowedPaths"])
        self.assertEqual(record["documentChangedPaths"], [])

    def test_global_presentation_needs_strict_separate_confirmation(self):
        self.denied("/editor/global-presentation", {"showBudgetDetails": False}, 422)
        self.denied("/editor/global-presentation", {"confirmGlobal": 1, "showBudgetDetails": False}, 422)
        self.denied("/editor/global-presentation", {"confirmGlobal": False, "showBudgetDetails": False}, 422)
        trip = deepcopy(self.session["tripState"])
        self.accepted("/editor/global-presentation", {"confirmGlobal": True, "showBudgetDetails": False})
        self.assertEqual(self.session["tripState"], trip)
        self.assertEqual(self.rows()[0]["payloadChangedPaths"], ["/presentationSettings/showBudgetDetails"])

    def test_constraints_only_explicit_change_and_not_lower_than_current_budget(self):
        self.denied("/editor/constraints", {"maxBudget": 3000}, 422)
        self.denied("/editor/constraints", {"confirmConstraintChange": True, "maxBudget": 2000}, 400)
        self.cap(); self.cap(None)
        self.assertFalse(any(c["kind"] == "max_budget" for c in self.session["persistedConstraints"]["hardConstraints"]))
        self.assertTrue(any(c["kind"] == "time_order" and c["active"] for c in self.session["persistedConstraints"]["hardConstraints"]))

    def test_restore_new_version_restores_data_ui_and_constraints_without_fake_candidate(self):
        day1 = self.binding("DayCard", "day1")
        self.accepted("/editor/locks", {"componentId": day1["componentId"], "locked": True}, "patch")
        self.cap()
        target = self.session["version"]; snapshot = self.rows()[0]
        self.accepted("/editor/global-presentation", {"confirmGlobal": True, "showBudgetDetails": False})
        self.accepted("/editor/locks", {"componentId": day1["componentId"], "locked": False}, "patch")
        previous = self.session["version"]
        self.accepted("/versions/restore", {"targetVersion": target, "confirmRestore": True})
        self.assertEqual(self.session["version"], previous + 1)
        self.assertEqual(payload_snapshot(self.stored()), snapshot["payloadSnapshot"])
        record = self.rows()[0]
        self.assertEqual(record["restoreFromVersion"], target)
        self.assertEqual(record["parentVersion"], previous)
        self.assertIsNone(record["candidateId"])
        self.assertEqual(record["patch"], [])
        self.assertEqual(record["constraintsSnapshot"], snapshot["constraintsSnapshot"])

    def test_restore_requires_confirmation_and_rejects_old_version_atomically(self):
        self.denied("/versions/restore", {"targetVersion": 1}, 422)
        self.denied("/versions/restore", {"targetVersion": 999, "confirmRestore": True}, 404)
        old_version = self.session["version"]
        self.cap()
        response = self.denied("/versions/restore", {"targetVersion": 1, "confirmRestore": True, "baseVersion": old_version}, 409)
        self.assertIn("v1", response.json()["detail"]["message"])
        self.assertIn("v2", response.json()["detail"]["message"])

    def test_diff_direction_and_specific_leaf_values(self):
        self.accepted("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "休息"}, "patch")
        response = self.client.get(self.url + "/versions/2/diff?againstVersion=1")
        result = response.json()
        self.assertEqual((result["fromVersion"], result["toVersion"]), (1, 2))
        self.assertEqual(result["differences"], [{"path": "/tripState/days/day2/note", "before": "", "after": "休息"}])

    def test_restart_preserves_immutable_history_and_constraints(self):
        self.cap()
        self.accepted("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "persistent"}, "patch")
        before = self.stored(); rows = self.rows()
        module.store = Store(Path(self.directory.name))
        self.assertEqual(module.store.get(self.session["id"]), before)
        self.assertEqual(self.rows(), rows)
        self.assertEqual(self.client.get(self.url).json(), self.session)
        self.assertEqual(self.client.get(self.url + "/versions").json()["currentVersion"], 3)

    def test_history_and_restore_are_bound_to_owner(self):
        second = TestClient(module.app, raise_server_exceptions=False)
        self.addCleanup(second.close)
        self.assertEqual(second.get(self.url + "/versions").status_code, 404)
        self.assertEqual(second.get(self.url + "/versions/1").status_code, 404)
        self.assertEqual(second.post(self.url + "/versions/restore", json={"baseVersion": 1, "targetVersion": 1, "confirmRestore": True}).status_code, 404)

    def test_ten_rounds_keep_locked_business_and_hard_constraints_unchanged(self):
        for day in ("day1", "day3"):
            self.accepted("/editor/locks", {"componentId": self.binding("DayCard", day)["componentId"], "locked": True}, "patch")
        self.cap()
        baseline = deepcopy(self.session)
        for index in range(10):
            before_version = self.session["version"]
            if index in (0, 3, 5, 7):
                batch = self.batch(); self.accepted("/edits/commit", {"candidateId": batch["candidates"][0]["candidateId"]})
            elif index in (1, 6):
                self.accepted("/editor/global-presentation", {"confirmGlobal": True, "showBudgetDetails": index == 6})
            elif index in (2, 4):
                self.accepted("/editor/display", {"selectedComponentId": self.day2["componentId"], "compact": index == 2})
            elif index == 8:
                self.accepted("/versions/restore", {"targetVersion": baseline["version"] + 1, "confirmRestore": True})
            else:
                self.accepted("/undo", {})
            self.assertEqual(self.session["version"], before_version + 1)
            for day in ("day1", "day3"):
                self.assertEqual(self.session["tripState"]["days"][day], baseline["tripState"]["days"][day])
            self.assertEqual(self.session["persistedConstraints"], baseline["persistedConstraints"])
            self.assertEqual(self.rows()[0]["version"], self.session["version"])

    def test_explicit_undo_keeps_chain_and_continuous_undo_semantics(self):
        self.accepted("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "one"}, "patch")
        self.accepted("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "two"}, "patch")
        self.accepted("/undo", {}); self.assertEqual(self.session["tripState"]["days"]["day2"]["note"], "one")
        self.accepted("/undo", {}); self.assertEqual(self.session["tripState"]["days"]["day2"]["note"], "")
        self.assertEqual([r["version"] for r in self.rows()], [5, 4, 3, 2, 1])
        self.assertEqual(self.rows()[0]["restoreFromVersion"], 1)

    def test_non_candidate_edit_does_not_reuse_previous_candidate_metadata(self):
        batch = self.batch(); self.accepted("/edits/commit", {"candidateId": batch["candidates"][0]["candidateId"]})
        self.accepted("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "manual"}, "patch")
        self.assertIsNone(self.rows()[0]["candidateId"])
        self.assertEqual(self.rows()[0]["patch"], [])
        self.assertEqual(self.rows()[0]["allowedPaths"], ["/tripState/days/day2/note"])

    def test_direct_budget_and_restore_changed_paths_use_declared_payload_namespace(self):
        self.accepted("/direct", {"kind": "budget", "value": 2800}, "patch")
        record = self.rows()[0]
        self.assertEqual(record["changedPaths"], ["/tripState/budgetLimit"])
        self.assertEqual(record["changedPaths"], record["payloadChangedPaths"])
        self.accepted("/versions/restore", {"targetVersion": 1, "confirmRestore": True})
        record = self.rows()[0]
        self.assertEqual(record["changedPaths"], record["payloadChangedPaths"])
        self.assertEqual(record["pathNamespaces"]["changedPaths"], "payloadSnapshot")
        from backend.versioning import SNAPSHOT_KEYS
        self.assertEqual(record["allowedPaths"], ["/" + key for key in SNAPSHOT_KEYS])

    def test_explicit_reset_restores_initial_constraints_not_current_budget_cap(self):
        self.accepted("/direct", {"kind": "budget", "value": 1200}, "patch")
        self.cap(1500)
        self.accepted("/reset", {})
        self.assertEqual(self.session["tripState"]["budgetLimit"], 2600)
        self.assertFalse(any(item["kind"] == "max_budget" for item in self.session["persistedConstraints"]["hardConstraints"]))

if __name__ == "__main__":
    unittest.main()
