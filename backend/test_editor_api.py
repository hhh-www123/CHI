"""Phase 1 HTTP integration/security regressions against isolated SQLite only.

No LLM/map services, browser profiles, source-project files or existing formal
session database are used. Run: python -m unittest backend.test_editor_api -v
"""
from __future__ import annotations

import atexit
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import access, agent, maps
from backend.config import Settings

_import_directory = tempfile.TemporaryDirectory(prefix="travel-editor-api-import-")
atexit.register(_import_directory.cleanup)
with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": _import_directory.name}):
    module = importlib.import_module("backend.app")


class EditorAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="travel-editor-api-test-")
        self.addCleanup(self.directory.cleanup)
        previous_store = module.store
        module.store = module.Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", previous_store)
        settings = Settings(agent_mode="demo", llm_api_key="", amap_key="", requests_per_minute=120)
        for owner in (module, agent, access, maps):
            setting_patch = patch.object(owner, "get_settings", return_value=settings)
            setting_patch.start()
            self.addCleanup(setting_patch.stop)
        with access._bucket_lock:
            access._buckets.clear()
        map_guard = patch.object(maps, "_request_once", side_effect=AssertionError("Phase 1 API tests must never query real maps"))
        self.network_guard = map_guard.start()
        self.addCleanup(map_guard.stop)
        self.client = TestClient(module.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/sessions", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.url = f"/api/sessions/{self.session['id']}"
        self.registry = self.session["componentRegistry"]
        self.pm = self.binding("PeriodGroup", dayKey="day2", period="pm")
        self.activity = next(item for item in self.registry.values()
                             if item["kind"] == "ActivityCard" and item["dayKey"] == "day2"
                             and self.session["tripDocument"]["entities"]["activities"][item["entityId"]]["startTime"] == "13:30")
        self.description_path = f"/entities/activities/{self.activity['entityId']}/description"
        self.time_path = f"/entities/activities/{self.activity['entityId']}/startTime"

    def binding(self, kind, **attributes):
        return next(item for item in self.registry.values()
                    if item["kind"] == kind and all(item.get(key) == value for key, value in attributes.items()))

    def stored(self):
        return deepcopy(module.store.get(self.session["id"]))

    def update(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session

    def lock(self, binding, locked=True):
        return self.update(self.client.patch(self.url + "/editor/locks", json={
            "baseVersion": self.session["version"], "componentId": binding["componentId"], "locked": locked,
        }))

    def preview(self, patch_ops=None, *, selection=None, fields=None, extra=None):
        data = {
            "baseVersion": self.session["version"],
            "selectedComponentId": (selection or self.pm)["componentId"],
            "fields": fields or ["description"],
            "patch": patch_ops if patch_ops is not None else [
                {"op": "replace", "path": self.description_path, "value": "缩短游览，安排一次休息。"},
            ],
        }
        data.update(extra or {})
        return self.client.post(self.url + "/editor/preview", json=data)

    def assert_rejected_unchanged(self, response, before, expected=(400, 403, 413, 422, 423)):
        self.assertIn(response.status_code, expected, response.text)
        self.assertEqual(self.stored(), before, "拒绝请求不能修改正式状态、版本、约束或历史")

    def test_document_and_scope_are_read_only_and_separate_constraints(self):
        before = self.stored()
        document = self.client.get(self.url + "/document")
        self.assertEqual(document.status_code, 200, document.text)
        body = document.json()
        self.assertEqual(body["document"], self.session["tripDocument"])
        self.assertEqual(body["constraints"], self.session["persistedConstraints"])
        self.assertEqual(body["registry"], self.registry)
        for forbidden in ("selectedComponentIds", "hoveredComponentId", "persistedConstraints", "editorMetadata"):
            self.assertNotIn(forbidden, body["document"])
        for component in body["document"]["ui"]["components"].values():
            self.assertEqual(component["props"], {})
        response = self.client.post(self.url + "/editor/scope", json={
            "baseVersion": self.session["version"], "selectedComponentId": self.pm["componentId"], "fields": ["description"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        scope = response.json()
        self.assertTrue(scope["allowedPaths"])
        self.assertIn(self.description_path, scope["allowedPaths"])
        self.assertTrue(all(path.endswith("/description") for path in scope["allowedPaths"]))
        eligible = {item["entityId"] for item in self.registry.values()
                    if item["kind"] == "ActivityCard" and item["dayKey"] == "day2" and item["period"] == "pm"}
        self.assertEqual(set(scope["eligibleActivityIds"]), eligible)
        self.assertEqual(self.stored(), before)

    def test_day_selection_authorises_only_that_days_registered_activities(self):
        before = self.stored()
        for day_key in ("day1", "day2", "day3"):
            with self.subTest(day=day_key):
                binding = self.binding("DayCard", dayKey=day_key)
                response = self.client.post(self.url + "/editor/scope", json={"baseVersion": self.session["version"], "selectedComponentId": binding["componentId"], "fields": ["description", "startTime"]})
                self.assertEqual(response.status_code, 200, response.text)
                expected = {path for item in self.registry.values() if item["kind"] == "ActivityCard" and item["dayKey"] == day_key for path in item["editablePaths"] if not path.endswith("/placeId")}
                self.assertTrue(expected)
                self.assertEqual(set(response.json()["allowedPaths"]), expected)
        self.assertEqual(self.stored(), before)

    def test_preview_then_apply_changes_only_selected_pm_description_with_other_days_locked(self):
        self.lock(self.binding("DayCard", dayKey="day1"))
        self.lock(self.binding("DayCard", dayKey="day3"))
        before = self.stored()
        old_public = deepcopy(self.session)
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(preview["generationMode"], "mock")
        self.assertEqual(preview["changedPaths"], [self.description_path])
        self.assertEqual(self.stored(), before, "预览不得写正式状态")
        self.assertEqual(self.client.get(self.url).json(), old_public)
        result = self.update(self.client.post(self.url + "/editor/apply", json={
            "baseVersion": self.session["version"], "previewId": preview["previewId"],
        }))
        expected = deepcopy(old_public["tripState"])
        target_stop = next(item for item in expected["days"]["day2"]["stops"] if item["id"] == self.activity["stopId"])
        target_stop["description"] = "缩短游览，安排一次休息。"
        self.assertEqual(result["tripState"], expected)
        self.assertEqual(result["uiSpec"], old_public["uiSpec"])
        self.assertEqual(result["derived"], old_public["derived"])
        self.assertEqual(result["persistedConstraints"], old_public["persistedConstraints"])
        self.assertEqual(result["version"], old_public["version"] + 1)
        self.assertEqual(result["editorAudit"]["changedPaths"], [self.description_path])
        self.assertTrue(result["editorAudit"]["validationReport"]["valid"])
        self.assertEqual(len(result["history"]), len(old_public["history"]) + 1)
        self.assertEqual(result["tripDocument"]["entities"]["activities"][self.activity["entityId"]]["description"], target_stop["description"])
        self.network_guard.assert_not_called()

    def test_time_patch_applies_only_selected_field_and_keeps_stable_ids(self):
        before = deepcopy(self.session)
        response = self.preview([{"op": "replace", "path": self.time_path, "value": "14:00"}], fields=["startTime"])
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(self.stored()["tripState"], before["tripState"])
        result = self.update(self.client.post(self.url + "/editor/apply", json={
            "baseVersion": self.session["version"], "previewId": preview["previewId"],
        }))
        expected = deepcopy(before["tripState"])
        next(item for item in expected["days"]["day2"]["stops"] if item["id"] == self.activity["stopId"])["startTime"] = "14:00"
        self.assertEqual(result["tripState"], expected)
        self.assertEqual(result["componentRegistry"], before["componentRegistry"])
        self.assertEqual(result["editorMetadata"], before["editorMetadata"])
        self.assertEqual(result["uiSpec"], before["uiSpec"])
        self.assertEqual(result["editorAudit"]["changedPaths"], [self.time_path])

    def test_authorization_is_narrowed_by_explicit_fields(self):
        before = self.stored()
        response = self.preview([{"op": "replace", "path": self.time_path, "value": "14:00"}], fields=["description"])
        self.assert_rejected_unchanged(response, before, (403,))
        response = self.preview(extra={"fields": ["placeId"]})
        self.assert_rejected_unchanged(response, before, (403,))
        response = self.preview(extra={"fields": ["title"]})
        self.assert_rejected_unchanged(response, before, (422,))

    def test_day_period_and_activity_locks_block_descendant_patch(self):
        for binding in (self.binding("DayCard", dayKey="day2"), self.pm, self.activity):
            with self.subTest(kind=binding["kind"]):
                self.lock(binding)
                before = self.stored()
                response = self.preview(selection=self.activity)
                self.assert_rejected_unchanged(response, before, (423,))
                scope = self.client.post(self.url + "/editor/scope", json={
                    "baseVersion": self.session["version"], "selectedComponentId": self.activity["componentId"], "fields": ["description"],
                })
                self.assertEqual(scope.status_code, 200, scope.text)
                self.assertTrue(scope.json()["locked"])
                self.assertEqual(scope.json()["allowedPaths"], [])
                self.lock(binding, False)

    def test_locked_child_is_removed_from_parent_scope(self):
        self.lock(self.activity)
        before = self.stored()
        scope = self.client.post(self.url + "/editor/scope", json={
            "baseVersion": self.session["version"], "selectedComponentId": self.pm["componentId"], "fields": ["description"],
        })
        self.assertEqual(scope.status_code, 200, scope.text)
        self.assertNotIn(self.description_path, scope.json()["allowedPaths"])
        self.assertTrue(scope.json()["allowedPaths"], "未锁定的下午景点仍应可编辑")
        self.assert_rejected_unchanged(self.preview(), before, (423,))

    def test_new_component_lock_cannot_be_bypassed_by_legacy_direct_input(self):
        self.lock(self.binding("DayCard", dayKey="day1"))
        self.lock(self.binding("DayCard", dayKey="day3"))
        for day_key, region in (("day1", "day-1"), ("day3", "day-3")):
            with self.subTest(day=day_key):
                before = self.stored()
                response = self.client.patch(self.url + "/direct", json={
                    "baseVersion": self.session["version"], "kind": "note", "targetRegionId": region, "value": "尝试越过组件锁",
                })
                self.assert_rejected_unchanged(response, before, (423,))
        self.lock(self.activity)
        before = self.stored()
        response = self.client.patch(self.url + "/direct", json={
            "baseVersion": self.session["version"], "kind": "time", "targetRegionId": "day-2",
            "stopId": self.activity["stopId"], "value": "14:00",
        })
        self.assert_rejected_unchanged(response, before, (423,))

    def test_new_component_lock_cannot_be_bypassed_by_legacy_candidate_apply(self):
        self.lock(self.binding("DayCard", dayKey="day1"))
        before = self.stored()
        generated = self.client.post(self.url + "/generate", json={
            "baseVersion": self.session["version"], "targetRegionId": "day-1",
            "instruction": "改成时间轴", "generateCandidates": False,
        })
        self.assertIn(generated.status_code, (200, 423), generated.text)
        if generated.status_code == 200:
            response = self.client.post(self.url + "/apply", json={
                "baseVersion": self.session["version"], "proposalId": generated.json()["proposalId"],
            })
            self.assert_rejected_unchanged(response, before, (423,))
        else:
            self.assertEqual(self.stored(), before)

    def test_legacy_generation_lock_retains_manual_input_semantics_but_blocks_patch(self):
        self.update(self.client.patch(self.url + "/locks", json={
            "baseVersion": self.session["version"], "target": "day-2", "locked": True,
        }))
        self.assertEqual(self.session["persistedConstraints"]["lockedComponentIds"], [])
        self.update(self.client.patch(self.url + "/direct", json={
            "baseVersion": self.session["version"], "kind": "note", "targetRegionId": "day-2", "value": "用户仍可直接填备注",
        }))
        before = self.stored()
        self.assert_rejected_unchanged(self.preview(), before, (423,))

    def test_mixed_authorized_and_other_day_operations_are_atomic(self):
        self.lock(self.binding("DayCard", dayKey="day1"))
        self.lock(self.binding("DayCard", dayKey="day3"))
        other = self.binding("ActivityCard", dayKey="day1", period="am")
        before = self.stored()
        response = self.preview([
            {"op": "replace", "path": self.description_path, "value": "合法第一步，但不能部分保存"},
            {"op": "replace", "path": f"/entities/activities/{other['entityId']}/description", "value": "未授权第二步"},
        ])
        self.assert_rejected_unchanged(response, before, (423,))
        self.assertFalse(module.store.proposals, "无效补丁不应被存为可应用预览")

    def test_parent_prefix_metadata_and_dangerous_pointers_are_rejected(self):
        prefix = self.description_path.rsplit("/", 1)[0]
        paths = [
            "", "/", "/entities", "/entities/activities", prefix,
            self.description_path + "Extra", prefix + "0/description", self.description_path + "/nested",
            "/version", "/schemaVersion", "/tripId", "/dayOrder/0", "/ui/components",
            "/persistedConstraints/lockedComponentIds", "/editorMetadata/activityPeriods", "/_owner",
            "/entities/activities/__proto__/description", "/entities/activities/constructor/description",
            "/entities/activities/prototype/description", prefix + "/description~2",
            prefix + "/description~1nested", prefix + "/description~0nested",
            prefix + "/%64escription", prefix + "//description", "#" + self.description_path,
            prefix + "/../description", prefix + "/-", prefix + "/description\u0000",
        ]
        for path in paths:
            with self.subTest(path=repr(path)):
                before = self.stored()
                response = self.preview([{"op": "replace", "path": path, "value": "非法修改"}])
                self.assert_rejected_unchanged(response, before)

    def test_forged_authorization_and_constraints_in_request_are_rejected(self):
        for forged in (
            {"allowedPaths": ["/version", "/tripState/days/day1"]},
            {"constraints": {"lockedComponentIds": []}},
            {"lockedComponentIds": []},
            {"registry": {self.pm["componentId"]: {"editablePaths": ["/version"]}}},
            {"selectedComponentIds": [self.pm["componentId"]]},
        ):
            with self.subTest(keys=list(forged)):
                before = self.stored()
                self.assert_rejected_unchanged(self.preview(extra=forged), before, (422,))

    def test_invalid_patch_operations_values_and_shape_do_not_change_state(self):
        invalid_ops = [
            [], [{}], ["replace"], [None],
            [{"op": "add", "path": self.description_path, "value": "x"}],
            [{"op": "remove", "path": self.description_path}],
            [{"op": "move", "from": self.time_path, "path": self.description_path}],
            [{"op": "copy", "from": self.time_path, "path": self.description_path}],
            [{"op": "test", "path": self.description_path, "value": "x"}],
            [{"op": "replace", "path": self.description_path}],
            [{"op": "replace", "path": self.description_path, "value": "x", "from": "/version"}],
            [{"op": "replace", "path": self.description_path, "value": {"__proto__": {"x": 1}}}],
            [{"op": "replace", "path": self.description_path, "value": None}],
            [{"op": "replace", "path": self.description_path, "value": 123}],
            [{"op": "replace", "path": self.description_path, "value": "x" * 2001}],
            [{"op": "replace", "path": self.description_path, "value": "x"}] * 2,
            [{"op": "replace", "path": self.description_path, "value": "x"}] * 17,
        ]
        for ops in invalid_ops:
            with self.subTest(operations=str(ops)[:110]):
                before = self.stored()
                self.assert_rejected_unchanged(self.preview(ops), before)

    def test_bad_time_schema_period_and_order_are_rejected_atomically(self):
        for value in ("25:30", "9:30", "13:99", "18:00", "16:00", "17:00"):
            with self.subTest(time=value):
                before = self.stored()
                response = self.preview([{"op": "replace", "path": self.time_path, "value": value}], fields=["startTime"])
                self.assert_rejected_unchanged(response, before)

    def test_stale_preview_cannot_overwrite_more_recent_state(self):
        generated = self.preview()
        self.assertEqual(generated.status_code, 200, generated.text)
        preview = generated.json()
        old_version = self.session["version"]
        self.update(self.client.patch(self.url + "/direct", json={
            "baseVersion": old_version, "kind": "note", "targetRegionId": "day-2", "value": "另一个操作的新状态",
        }))
        before = self.stored()
        for supplied_version in (old_version, self.session["version"]):
            with self.subTest(baseVersion=supplied_version):
                response = self.client.post(self.url + "/editor/apply", json={
                    "baseVersion": supplied_version, "previewId": preview["previewId"],
                })
                self.assert_rejected_unchanged(response, before, (409,))

    def test_lock_after_preview_makes_old_preview_unusable(self):
        generated = self.preview()
        self.assertEqual(generated.status_code, 200, generated.text)
        self.lock(self.pm)
        before = self.stored()
        response = self.client.post(self.url + "/editor/apply", json={
            "baseVersion": self.session["version"], "previewId": generated.json()["previewId"],
        })
        self.assert_rejected_unchanged(response, before, (409,))
        self.assert_rejected_unchanged(self.preview(), before, (423,))

    def test_apply_uses_cached_patch_and_rejects_client_replacement_or_reuse(self):
        generated = self.preview()
        self.assertEqual(generated.status_code, 200, generated.text)
        preview_id = generated.json()["previewId"]
        before = self.stored()
        tampered = self.client.post(self.url + "/editor/apply", json={
            "baseVersion": self.session["version"], "previewId": preview_id,
            "patch": [{"op": "replace", "path": "/version", "value": 900}],
        })
        self.assert_rejected_unchanged(tampered, before, (422,))
        self.update(self.client.post(self.url + "/editor/apply", json={"baseVersion": self.session["version"], "previewId": preview_id}))
        after = self.stored()
        reused = self.client.post(self.url + "/editor/apply", json={"baseVersion": self.session["version"], "previewId": preview_id})
        self.assert_rejected_unchanged(reused, after, (404,))

    def test_restart_preserves_constraints_business_values_and_component_ids(self):
        generated = self.preview()
        self.assertEqual(generated.status_code, 200, generated.text)
        self.update(self.client.post(self.url + "/editor/apply", json={
            "baseVersion": self.session["version"], "previewId": generated.json()["previewId"],
        }))
        self.lock(self.binding("DayCard", dayKey="day1"))
        self.lock(self.pm)
        before = self.stored()
        old_public = deepcopy(self.session)
        module.store = module.Store(Path(self.directory.name))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), old_public)
        self.assertEqual(self.stored(), before)
        scope = self.client.post(self.url + "/editor/scope", json={
            "baseVersion": self.session["version"], "selectedComponentId": self.pm["componentId"], "fields": ["description"],
        })
        self.assertEqual(scope.status_code, 200, scope.text)
        self.assertTrue(scope.json()["locked"])
        self.assertEqual(scope.json()["allowedPaths"], [])

    def test_new_api_respects_session_ownership_and_foreign_preview_ids(self):
        generated = self.preview()
        self.assertEqual(generated.status_code, 200, generated.text)
        preview_id = generated.json()["previewId"]
        before = self.stored()
        with TestClient(module.app, raise_server_exceptions=False) as outsider:
            for response in (
                outsider.get(self.url + "/document"),
                outsider.post(self.url + "/editor/scope", json={"baseVersion": 1, "selectedComponentId": self.pm["componentId"], "fields": ["description"]}),
                outsider.post(self.url + "/editor/preview", json={"baseVersion": 1, "selectedComponentId": self.pm["componentId"], "fields": ["description"], "patch": [{"op": "replace", "path": self.description_path, "value": " чужой"}]}),
                outsider.patch(self.url + "/editor/locks", json={"baseVersion": 1, "componentId": self.pm["componentId"], "locked": True}),
                outsider.post(self.url + "/editor/apply", json={"baseVersion": 1, "previewId": preview_id}),
            ):
                self.assert_rejected_unchanged(response, before, (404,))
            created = outsider.post("/api/sessions", json={})
            self.assertEqual(created.status_code, 200, created.text)
            foreign_id = created.json()["id"]
            attempted = outsider.post(f"/api/sessions/{foreign_id}/editor/apply", json={"baseVersion": 1, "previewId": preview_id})
            self.assertEqual(attempted.status_code, 404, attempted.text)
        self.assertEqual(self.stored(), before)

    def test_phase1_preview_id_is_rejected_by_legacy_apply_repair_and_map(self):
        generated = self.preview()
        self.assertEqual(generated.status_code, 200, generated.text)
        preview_id = generated.json()["previewId"]
        before = self.stored()
        responses = (
            self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": preview_id}),
            self.client.post(self.url + f"/proposals/{preview_id}/repair", json={"baseVersion": self.session["version"], "error": "malicious adapter call"}),
            self.client.get(self.url + f"/proposals/{preview_id}/map/day-2?v={self.session['version']}"),
        )
        for response in responses:
            self.assert_rejected_unchanged(response, before, (404,))
        self.assertIn(preview_id, module.store.proposals, "错误入口不能消费合法 P1 预览")

    def test_malformed_json_nonfinite_and_missing_selection_do_not_save(self):
        before = self.stored()
        for malformed in ("{", "null", "[]", '{"baseVersion":1,"patch":[]}'):
            with self.subTest(content=malformed):
                response = self.client.post(self.url + "/editor/preview", content=malformed, headers={"Content-Type": "application/json"})
                self.assert_rejected_unchanged(response, before, (422,))
        for special in ("NaN", "Infinity", "-Infinity"):
            content = json.dumps({"baseVersion": self.session["version"], "selectedComponentId": self.pm["componentId"],
                                  "fields": ["description"], "patch": [{"op": "replace", "path": self.description_path, "value": "SPECIAL_VALUE"}]})
            content = content.replace('"SPECIAL_VALUE"', special)
            response = self.client.post(self.url + "/editor/preview", content=content, headers={"Content-Type": "application/json"})
            self.assert_rejected_unchanged(response, before, (400, 422))
        self.assert_rejected_unchanged(self.preview(extra={"selectedComponentId": "not-a-component"}), before, (403,))

    def test_old_llm_edit_entrypoints_fail_before_any_network_client_is_created(self):
        llm_settings = Settings(agent_mode="llm", llm_api_key="test-only-not-a-real-secret", requests_per_minute=120)
        before = self.stored()
        with patch.object(module, "get_settings", return_value=llm_settings), \
             patch.object(agent, "get_settings", return_value=llm_settings), \
             patch.object(access, "get_settings", return_value=llm_settings), \
             patch.object(agent.httpx, "Client", side_effect=AssertionError("forbidden model network")) as network:
            response = self.client.post(self.url + "/generate", json={
                "baseVersion": self.session["version"], "targetRegionId": "day-2",
                "instruction": "为第二天新增一个交互清单", "generateCandidates": False,
            })
            self.assert_rejected_unchanged(response, before, (503,))
            for phase, model in (("scope", agent.EditScope), ("candidates", agent.CandidateBatch), ("plan", agent.Plan)):
                with self.subTest(phase=phase), self.assertRaises(HTTPException) as error:
                    agent._request_model({}, "", model, phase)
                self.assertEqual(error.exception.status_code, 503)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
