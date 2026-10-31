"""Candidate choice and bounded-edit regression tests; no external API calls.

Run: python -m unittest backend.test_candidates -v
"""
from __future__ import annotations

import atexit
from copy import deepcopy
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import access, agent
from backend.config import Settings

# The first app import constructs a Store. Never let a direct test invocation
# open a developer's real travel database before setUp replaces that store.
_import_directory = tempfile.TemporaryDirectory(prefix="travel-candidate-import-")
atexit.register(_import_directory.cleanup)
with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": _import_directory.name}):
    module = importlib.import_module("backend.app")


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="travel-candidate-test-")
        self.addCleanup(self.directory.cleanup)
        self.previous_store = module.store
        module.store = module.Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", self.previous_store)
        self.settings = Settings(agent_mode="llm", llm_api_key="local-fixture-key", requests_per_minute=120)
        for owner in (module, agent, access):
            patcher = patch.object(owner, "get_settings", return_value=self.settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        with access._bucket_lock:
            access._buckets.clear()
        self.scope_value = self.scope_for_maps()
        self.plans = self.map_candidates()
        self.before_candidates = None
        self.scope_patch = patch.object(agent, "scope_request", side_effect=self.scope_response)
        self.scope_mock = self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)
        self.plans_patch = patch.object(agent, "candidates_request", side_effect=self.candidate_response)
        self.plans_mock = self.plans_patch.start()
        self.addCleanup(self.plans_patch.stop)
        provider_guard = patch.object(agent, "_request_model", side_effect=AssertionError("candidate fixtures must never contact a model provider"))
        provider_guard.start()
        self.addCleanup(provider_guard.stop)
        self.route_patch = patch.object(module, "enrich_day", side_effect=self.ready_route)
        self.route_mock = self.route_patch.start()
        self.addCleanup(self.route_patch.stop)
        self.image_patch = patch.object(module, "static_map_image", return_value=(b"candidate-map-fixture", "image/png"))
        self.image_mock = self.image_patch.start()
        self.addCleanup(self.image_patch.stop)
        self.client = TestClient(module.app)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/sessions")
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.url = f"/api/sessions/{self.session['id']}"

    @staticmethod
    def permission(region="day-2", **values):
        return {"regionId": region, **values}

    @classmethod
    def scope_for_maps(cls):
        return {
            "message": "仅为第二天添加交通与地图，不改原行程和其他展示。",
            "permissions": [cls.permission(uiFields=["showMap", "showTransport", "mapPlacement", "transportStyle"], requiredUI={"showMap": True, "showTransport": True})],
            "clarification": None,
            "unsupportedCapabilities": [],
        }

    @staticmethod
    def plan(title, operations, scope="selected"):
        return {"title": title, "rationale": "仅调整本次允许的内容。", "message": title, "scope": scope, "operations": operations, "clarification": None, "unsupportedCapabilities": []}

    @classmethod
    def map_candidates(cls):
        return [
            cls.plan("地图放在行程前", [{"kind": "set_ui", "regionId": "day-2", "showMap": True, "showTransport": True, "mapPlacement": "beforeStops", "transportStyle": "inline"}]),
            cls.plan("地图放在行程后", [{"kind": "set_ui", "regionId": "day-2", "showMap": True, "showTransport": True, "mapPlacement": "afterStops", "transportStyle": "inline"}]),
            cls.plan("交通集中查看", [{"kind": "set_ui", "regionId": "day-2", "showMap": True, "showTransport": True, "mapPlacement": "afterStops", "transportStyle": "summary"}]),
        ]

    def scope_response(self, *_args, **_kwargs):
        return agent.EditScope.model_validate(deepcopy(self.scope_value))

    def candidate_response(self, *_args, **_kwargs):
        if self.before_candidates:
            callback, self.before_candidates = self.before_candidates, None
            callback()
        return agent.CandidateBatch.model_validate({"message": "在允许范围内比较候选。", "candidates": deepcopy(self.plans)})

    @staticmethod
    def ready_route(day, places, _city, mode):
        return {
            "status": "ready", "provider": "amap", "message": "模拟路线用于验证数据流。", "queriedAt": "2026-10-26T00:00:00Z",
            "points": [{"stopId": stop["id"], "name": places[stop["placeId"]]["name"], "status": "resolved", "lng": 120.1 + index / 100, "lat": 30.2 + index / 100, "searchUrl": "https://uri.amap.com/search"} for index, stop in enumerate(day["stops"])],
            "segments": [{"fromStopId": first["id"], "toStopId": second["id"], "fromName": places[first["placeId"]]["name"], "toName": places[second["placeId"]]["name"], "mode": mode, "status": "ready", "durationMinutes": 20, "distanceMeters": 2000, "summary": "模拟路线", "path": [], "navigationUrl": None} for first, second in zip(day["stops"], day["stops"][1:])],
        }

    def current(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def mutate(self, endpoint, values, method="post"):
        response = getattr(self.client, method)(self.url + endpoint, json={"baseVersion": self.session["version"], **values})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session

    def preview(self, instruction="只在第二天添加交通方式和地图，保留景点、时间、备注及其他展示。", target="day-2"):
        return self.client.post(self.url + "/generate", json={"baseVersion": self.session["version"], "targetRegionId": target, "instruction": instruction})

    def batch(self, **values):
        response = self.preview(**values)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["candidates"])
        return result

    def apply(self, candidate):
        return self.mutate("/apply", {"proposalId": candidate["proposalId"]})

    def test_three_distinct_previews_do_not_mutate_session_or_query_same_route_three_times(self):
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "下午留出休息时间"}, "patch")
        before = deepcopy(self.current())
        batch = self.batch()
        candidates = batch["candidates"]
        self.assertEqual(len(candidates), 3)
        self.assertEqual(len({candidate["proposalId"] for candidate in candidates}), 3)
        self.assertEqual(self.current(), before, "生成与切换候选均不能先写入行程")
        self.assertTrue(batch["scopeSummary"])
        self.assertTrue(batch["preservedSummary"])
        self.route_mock.assert_called_once()
        baseline_ui = before["uiSpec"]["regions"]["day-2"]
        permitted = {"showMap", "showTransport", "mapPlacement", "transportStyle"}
        for candidate in candidates:
            self.assertEqual(candidate["baseVersion"], before["version"])
            self.assertEqual(candidate["affectedRegionIds"], ["day-2"])
            update = candidate["updates"][0]
            self.assertNotIn("nextData", update)
            self.assertTrue(update["nextUI"]["showMap"])
            self.assertTrue(update["nextUI"]["showTransport"])
            for field, value in baseline_ui.items():
                if field not in permitted:
                    self.assertEqual(update["nextUI"][field], value, field)

    def test_user_can_apply_third_choice_and_undo_without_applying_siblings(self):
        before = deepcopy(self.current())
        candidates = self.batch()["candidates"]
        chosen = candidates[2]
        self.apply(chosen)
        self.assertEqual(self.session["uiSpec"]["regions"]["day-2"], chosen["updates"][0]["nextUI"])
        self.assertEqual(self.session["tripState"], before["tripState"])
        for region in ("day-1", "day-3"):
            self.assertEqual(self.session["uiSpec"]["regions"][region], before["uiSpec"]["regions"][region])
        state_after_apply = deepcopy(self.current())
        sibling = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": candidates[0]["proposalId"]})
        self.assertIn(sibling.status_code, (404, 409))
        self.assertEqual(self.current(), state_after_apply)
        self.mutate("/undo", {})
        for key in ("tripState", "uiSpec", "routeData", "controlState"):
            self.assertEqual(self.session[key], before[key], key)

    def test_candidates_cannot_expand_days_or_change_unrequested_ui_fields(self):
        bad_fields = self.map_candidates()[0]
        bad_fields["operations"][0].update({"view": "timeline", "showCost": False, "compact": True})
        outside_day = self.plan("声称修改全部日期", [{"kind": "set_ui", "regionId": region, "showMap": True, "showTransport": True} for region in module.REGIONS], scope="all_days")
        self.plans = [bad_fields, outside_day, self.map_candidates()[1]]
        before = deepcopy(self.current())
        batch = self.batch()
        self.assertEqual(len(batch["candidates"]), 1)
        self.assertEqual(batch["candidates"][0]["title"], self.map_candidates()[1]["title"])
        self.assertTrue(batch["candidateNotes"], "需说明越界候选被丢弃，而非伪装成多个有效结果")
        self.apply(batch["candidates"][0])
        for field in ("view", "showCost", "compact"):
            self.assertEqual(self.session["uiSpec"]["regions"]["day-2"][field], before["uiSpec"]["regions"]["day-2"][field])
        self.assertEqual(self.session["tripState"], before["tripState"])

    def test_scope_for_one_stop_does_not_allow_other_stops_times_notes_or_days(self):
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "已有安排不能覆盖"}, "patch")
        self.scope_value["permissions"] = [self.permission(placeStopIds=["day2-stop-1"])]
        self.plans = [
            self.plan("备选一", [{"kind": "replace_stop", "regionId": "day-2", "stopId": "day2-stop-1", "placeId": "wetland"}]),
            self.plan("备选二", [{"kind": "replace_stop", "regionId": "day-2", "stopId": "day2-stop-1", "placeId": "city-museum"}]),
            self.plan("越界的第二个景点", [{"kind": "replace_stop", "regionId": "day-2", "stopId": "day2-stop-2", "placeId": "silk-museum"}]),
        ]
        before = deepcopy(self.current())
        batch = self.batch(instruction="只替换第二天第一个景点，其他景点、时间、备注不要改。")
        self.assertEqual(len(batch["candidates"]), 2)
        self.apply(batch["candidates"][1])
        expected = deepcopy(before["tripState"])
        expected["days"]["day2"]["stops"][0]["placeId"] = "city-museum"
        self.assertEqual(self.session["tripState"], expected)
        self.assertEqual(self.session["uiSpec"], before["uiSpec"])
        self.route_mock.assert_not_called()

    def test_different_titles_and_unchanged_result_do_not_create_fake_choices(self):
        self.scope_value["permissions"] = [self.permission(uiFields=["view"])]
        self.plans = [
            self.plan("方案甲", [{"kind": "set_ui", "regionId": "day-2", "view": "timeline"}]),
            self.plan("同一结果换个名字", [{"kind": "set_ui", "regionId": "day-2", "view": "timeline"}]),
            self.plan("原来的卡片", [{"kind": "set_ui", "regionId": "day-2", "view": "cards"}]),
        ]
        before = deepcopy(self.current())
        result = self.batch(instruction="把第二天改成时间轴，其他内容不变。")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertTrue(result["candidateNotes"])
        self.assertEqual(self.current(), before)

    def test_unrendered_block_fields_do_not_make_visually_identical_choices(self):
        samples = (
            ("note", {"text": "同一可见说明", "items": ["旧隐藏项"], "columns": ["旧列"], "rows": [["旧值"]]},
             {"items": ["不同隐藏项"], "columns": ["不同列"], "rows": [["不同值"]]}, {"text": "另一段可见说明"}),
            ("checklist", {"text": "兼容原有正文", "items": ["相同可见清单项"], "columns": ["旧列"], "rows": [["旧值"]]},
             {"columns": ["不同列"], "rows": [["不同值"]]}, {"items": ["另一条可见清单项"]}),
            ("table", {"text": "兼容原有正文", "items": ["旧隐藏项"], "columns": ["项目"], "rows": [["相同可见值"]]},
             {"items": ["不同隐藏项"]}, {"rows": [["另一项可见值"]]}),
        )
        for block_type, content, unused_changes, visible_changes in samples:
            with self.subTest(block_type=block_type):
                self.scope_value["permissions"] = [self.permission(newBlockTypes=[block_type])]
                first = {"id": block_type + "-first", "type": block_type, "title": "相同可见标题", **content}
                duplicate = {**deepcopy(first), "id": block_type + "-duplicate", **unused_changes}
                distinct = {**deepcopy(first), "id": block_type + "-distinct", **visible_changes}
                self.plans = [self.plan(title, [{"kind": "upsert_block", "regionId": "day-2", "block": block}])
                              for title, block in (("原始可见内容", first), ("只改未展示字段", duplicate), ("改变可见内容", distinct))]
                before = deepcopy(self.current())
                result = self.batch(instruction="只给第二天增加补充内容，已有行程不变。")
                self.assertEqual([candidate["title"] for candidate in result["candidates"]], ["原始可见内容", "改变可见内容"])
                self.assertTrue(any("重复" in note for note in result["candidateNotes"]))
                self.assertEqual(result["candidates"][0]["updates"][0]["nextUI"]["blocks"][-1], first,
                                 "Fingerprint normalization must not strip legacy fields from the actual candidate")
                self.assertEqual(self.current(), before)
        self.route_mock.assert_not_called()

    def test_required_request_cannot_be_ignored_to_create_extra_variations(self):
        missing_transport = self.plan("遗漏交通", [{"kind": "set_ui", "regionId": "day-2", "showMap": True}])
        wrong_map_value = self.plan("关闭地图", [{"kind": "set_ui", "regionId": "day-2", "showMap": False, "showTransport": True}])
        self.plans = [missing_transport, wrong_map_value, self.map_candidates()[0]]
        result = self.batch()
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["title"], self.map_candidates()[0]["title"])

    def test_all_invalid_candidates_make_no_proposal_no_route_query_and_no_mutation(self):
        self.plans = [self.plan("改了无关字段", [{"kind": "set_ui", "regionId": "day-2", "showCost": False}])]
        before = deepcopy(self.current())
        response = self.preview()
        self.assertIn(response.status_code, (400, 502), response.text)
        self.assertEqual(self.current(), before)
        self.assertFalse(module.store.proposals)
        self.route_mock.assert_not_called()

    def test_a_day_mentioned_only_to_preserve_it_is_not_an_edit_permission(self):
        self.scope_value["permissions"] = [self.permission(region, uiFields=["showMap"], requiredUI={"showMap": True}) for region in ("day-1", "day-2")]
        before = deepcopy(self.current())
        for instruction in (
            "仅修改第二天地图，第一天保持不变。",
            "第二天添加地图，第一天不要改。",
            "只改第二天地图，不改第一天。",
        ):
            with self.subTest(instruction=instruction):
                response = self.preview(instruction)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.current(), before)
        self.plans_mock.assert_not_called()
        self.route_mock.assert_not_called()

    def test_all_days_request_requires_each_requested_day_not_just_one(self):
        self.scope_value["permissions"] = [self.permission(region, uiFields=["showMap", "mapPlacement"], requiredUI={"showMap": True}) for region in module.REGIONS]
        valid = self.plan("三天地图", [{"kind": "set_ui", "regionId": region, "showMap": True, "mapPlacement": "beforeStops"} for region in module.REGIONS], scope="all_days")
        incomplete = self.plan("只改一天", [{"kind": "set_ui", "regionId": "day-2", "showMap": True}], scope="all_days")
        self.plans = [incomplete, valid]
        result = self.batch(instruction="为每天添加地图，其他内容不变。", target="all")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(set(result["candidates"][0]["affectedRegionIds"]), set(module.REGIONS))
        self.assertEqual(self.route_mock.call_count, 3)

    def test_editing_one_existing_block_does_not_allow_removing_or_overwriting_others(self):
        def block(block_id, text):
            return {"id": block_id, "type": "note", "title": block_id, "text": text, "items": [], "columns": [], "rows": []}
        current = module.store.get(self.session["id"])
        initial = module.payload_of(current)
        initial["uiSpec"]["regions"]["day-2"]["blocks"] = [block("packing", "原始清单"), block("reservation", "预订号码要保留")]
        module.commit(current, initial, "测试现有内容块", "direct")
        self.session = self.current()
        self.scope_value["permissions"] = [self.permission(editableBlockIds=["packing"])]
        self.plans = [
            self.plan("更新随身清单", [{"kind": "upsert_block", "regionId": "day-2", "block": block("packing", "带好雨伞和充电宝")}]),
            self.plan("删除无关内容", [{"kind": "remove_block", "regionId": "day-2", "blockId": "reservation"}]),
            self.plan("添加未要求内容", [{"kind": "upsert_block", "regionId": "day-2", "block": block("shopping", "无关购物建议")}]),
        ]
        before = deepcopy(self.current())
        result = self.batch(instruction="只修改第二天的随身清单，不改其他内容。")
        self.assertEqual(len(result["candidates"]), 1)
        self.apply(result["candidates"][0])
        blocks = {item["id"]: item for item in self.session["uiSpec"]["regions"]["day-2"]["blocks"]}
        self.assertEqual(set(blocks), {"packing", "reservation"})
        self.assertEqual(blocks["reservation"], before["uiSpec"]["regions"]["day-2"]["blocks"][1])
        self.assertEqual(blocks["packing"]["text"], "带好雨伞和充电宝")
        self.assertEqual(self.session["tripState"], before["tripState"])

    def test_presentation_only_candidates_reuse_visible_routes_without_new_provider_queries(self):
        self.mutate("/routes", {"targetRegionId": "day-2"})
        before = deepcopy(self.current())
        self.route_mock.reset_mock()
        self.scope_value["permissions"] = [self.permission(uiFields=["view", "compact"])]
        self.plans = [
            self.plan("时间轴", [{"kind": "set_ui", "regionId": "day-2", "view": "timeline"}]),
            self.plan("紧凑卡片", [{"kind": "set_ui", "regionId": "day-2", "compact": True}]),
            self.plan("紧凑时间轴", [{"kind": "set_ui", "regionId": "day-2", "view": "timeline", "compact": True}]),
        ]
        result = self.batch(instruction="调整第二天展示方式，现有交通地图和行程保留。")
        self.assertEqual(len(result["candidates"]), 3)
        self.route_mock.assert_not_called()
        self.apply(result["candidates"][2])
        self.assertEqual(self.session["routeData"], before["routeData"])
        self.assertEqual(self.session["tripState"], before["tripState"])
        self.route_mock.assert_not_called()

    def test_apply_rechecks_scope_instead_of_trusting_a_stored_candidate(self):
        result = self.batch()
        candidate = result["candidates"][0]
        entry = module.store.proposals[candidate["proposalId"]]
        entry["value"]["updates"][0]["nextUI"]["showCost"] = False
        before = deepcopy(self.current())
        response = self.client.post(self.url + "/apply", json={"baseVersion": before["version"], "proposalId": candidate["proposalId"]})
        self.assertIn(response.status_code, (400, 502), response.text)
        self.assertEqual(self.current(), before)

    def test_presentation_only_apply_cannot_replace_route_data_outside_requested_scope(self):
        self.mutate("/routes", {"targetRegionId": "day-2"})
        self.scope_value["permissions"] = [self.permission(uiFields=["view"])]
        self.plans = [self.plan("时间轴", [{"kind": "set_ui", "regionId": "day-2", "view": "timeline"}])]
        result = self.batch(instruction="只把第二天改成时间轴，路线和行程保留。")
        candidate = result["candidates"][0]
        entry = module.store.proposals[candidate["proposalId"]]
        entry["value"]["updates"][0]["routeData"] = {"status": "unavailable", "provider": "amap", "message": "无关路线被覆盖", "points": [], "segments": []}
        before = deepcopy(self.current())
        response = self.client.post(self.url + "/apply", json={"baseVersion": before["version"], "proposalId": candidate["proposalId"]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.current(), before)

    def test_hidden_transport_preference_change_expires_old_routes_before_map_is_shown_again(self):
        self.mutate("/routes", {"targetRegionId": "day-2"})
        current = module.store.get(self.session["id"])
        hidden = module.payload_of(current)
        hidden["uiSpec"]["regions"]["day-2"].update({"showMap": False, "showTransport": False})
        module.commit(current, hidden, "隐藏路线展示，保留可复用缓存", "direct")
        self.session = self.current()
        self.route_mock.reset_mock()
        self.scope_value["permissions"] = [self.permission(uiFields=["transportMode"], requiredUI={"transportMode": "walking"})]
        self.plans = [self.plan("改为步行偏好", [{"kind": "set_ui", "regionId": "day-2", "transportMode": "walking"}])]
        result = self.batch(instruction="将第二天的交通偏好改为步行，暂不显示地图或交通。")
        self.route_mock.assert_not_called()
        self.apply(result["candidates"][0])
        self.assertNotIn("day-2", self.session["routeData"], "隐藏组件的交通模式变化也会使旧路线过期")
        self.scope_value["permissions"] = [self.permission(uiFields=["showMap"], requiredUI={"showMap": True})]
        self.plans = [self.plan("显示步行地图", [{"kind": "set_ui", "regionId": "day-2", "showMap": True}])]
        result = self.batch(instruction="显示第二天的路线地图，保留当前交通偏好。")
        self.route_mock.assert_called_once()
        self.assertEqual(self.route_mock.call_args.args[3], "walking")
        self.apply(result["candidates"][0])
        self.assertTrue(all(segment["mode"] == "walking" for segment in self.session["routeData"]["day-2"]["segments"]))

    def test_concurrent_edit_during_generation_rejects_entire_batch(self):
        def concurrent_edit():
            current = module.store.get(self.session["id"])
            candidate = module.payload_of(current)
            candidate["tripState"]["days"]["day2"]["note"] = "另一个窗口的新备注"
            module.commit(current, candidate, "并发编辑", "direct")
        self.before_candidates = concurrent_edit
        response = self.preview()
        self.assertEqual(response.status_code, 409, response.text)
        current = self.current()
        self.assertEqual(current["tripState"]["days"]["day2"]["note"], "另一个窗口的新备注")
        self.assertFalse(current["uiSpec"]["regions"]["day-2"]["showMap"])
        self.assertFalse(module.store.proposals)

    def test_locked_region_cannot_generate_candidates_and_later_lock_expires_a_preview(self):
        self.mutate("/locks", {"target": "day-2", "locked": True}, "patch")
        response = self.preview()
        self.assertEqual(response.status_code, 423, response.text)
        self.plans_mock.assert_not_called()
        self.route_mock.assert_not_called()
        self.mutate("/locks", {"target": "day-2", "locked": False}, "patch")
        candidate = self.batch()["candidates"][0]
        self.mutate("/locks", {"target": "day-2", "locked": True}, "patch")
        before = deepcopy(self.current())
        response = self.client.post(self.url + "/apply", json={"baseVersion": before["version"], "proposalId": candidate["proposalId"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.current(), before)

    def test_candidate_map_and_apply_are_bound_to_owner_session_and_base_version(self):
        candidate = self.batch()["candidates"][0]
        proposal_id = candidate["proposalId"]
        map_path = self.url + f"/proposals/{proposal_id}/map/day-2?v={self.session['version']}"
        own = self.client.get(map_path)
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.headers["content-type"], "image/png")
        self.assertEqual(own.content, b"candidate-map-fixture")
        self.image_mock.assert_called_once()
        foreign = TestClient(module.app)
        self.addCleanup(foreign.close)
        self.assertEqual(foreign.get(map_path).status_code, 404)
        self.assertEqual(foreign.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": proposal_id}).status_code, 404)
        other = self.client.post("/api/sessions").json()
        other_url = f"/api/sessions/{other['id']}"
        self.assertEqual(self.client.get(other_url + f"/proposals/{proposal_id}/map/day-2?v=1").status_code, 404)
        self.assertEqual(self.client.post(other_url + "/apply", json={"baseVersion": 1, "proposalId": proposal_id}).status_code, 404)
        self.mutate("/direct", {"kind": "note", "targetRegionId": "day-2", "value": "预览后的新备注"}, "patch")
        self.assertEqual(self.client.get(map_path).status_code, 409)
        self.image_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
