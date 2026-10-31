"""Date-boundary regressions: preserve a field without excluding its day."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import agent
from backend import scope as guard
from backend.config import Settings


class ScopeDateTests(unittest.TestCase):
    def snapshot(self):
        return {
            "version": 1,
            "tripState": {
                "city": "杭州",
                "budgetLimit": 2600,
                "hotelId": "lake-hotel",
                "nights": 2,
                "days": {key: {"date": f"2026-10-{26 + index}", "title": key, "note": "", "stops": [{"id": f"{key}-stop-1", "placeId": "west-lake", "startTime": "09:30"}]} for index, key in enumerate(guard.REGIONS.values())},
            },
            "uiSpec": {"regions": {region: {"showMap": False, "blocks": []} for region in guard.REGIONS}},
            "controlState": {"lockedRegions": [], "hotelLocked": False},
        }

    def scope(self, regions):
        return agent.EditScope(message="只添加地图", permissions=[agent.RegionPermission(regionId=region, uiFields=["showMap"], requiredUI=agent.UIValues(showMap=True)) for region in regions])

    def test_preserving_second_day_pois_still_allows_its_map_to_be_added(self):
        for target in ("all", "day-1", "day-2"):
            with self.subTest(target=target):
                instruction = "第二天景点不变，添加地图"
                self.assertEqual(guard.requested_regions(instruction, target), {"day-2"})
                frozen = guard.freeze_scope(self.snapshot(), self.scope(["day-2"]), instruction, target)
                self.assertEqual([permission.regionId for permission in frozen.permissions], ["day-2"])
                self.assertFalse(frozen.permissions[0].placeStopIds)

    def test_daily_request_excludes_only_an_explicitly_protected_whole_day(self):
        for instruction in ("每天加地图，第一天不改", "每天加地图，第一天保持原样", "每天加地图，不要修改第一天"):
            with self.subTest(instruction=instruction):
                self.assertEqual(guard.requested_regions(instruction, "all"), {"day-2", "day-3"})
                frozen = guard.freeze_scope(self.snapshot(), self.scope(["day-2", "day-3"]), instruction, "all")
                self.assertEqual({permission.regionId for permission in frozen.permissions}, {"day-2", "day-3"})
                with self.assertRaises(HTTPException) as failure:
                    guard.freeze_scope(self.snapshot(), self.scope(["day-1", "day-2", "day-3"]), instruction, "all")
                self.assertEqual(failure.exception.status_code, 400)

    def test_excluding_one_day_does_not_allow_omitting_another_requested_day(self):
        with self.assertRaises(HTTPException) as failure:
            guard.freeze_scope(self.snapshot(), self.scope(["day-2"]), "每天加地图，第一天不改", "all")
        self.assertEqual(failure.exception.status_code, 502)

    def test_preserving_one_field_does_not_remove_a_day_from_a_daily_request(self):
        for instruction in ("每天加地图，第一天景点不变", "每天加地图，不要改第一天的备注"):
            with self.subTest(instruction=instruction):
                self.assertEqual(guard.requested_regions(instruction, "all"), set(guard.REGIONS))

    def test_preserved_field_context_cannot_broaden_an_explicit_only_day(self):
        instruction = "只改第二天，第一天备注保留，添加地图"
        self.assertEqual(guard.requested_regions(instruction, "all"), {"day-2"})

    def test_protective_date_mentions_do_not_override_the_actual_target(self):
        for instruction in ("第二天加地图，第一天景点不变", "保留第一天备注，第二天加地图", "第二天景点不变并添加地图"):
            with self.subTest(instruction=instruction):
                self.assertEqual(guard.requested_regions(instruction, "all"), {"day-2"})

    def test_all_protected_days_result_in_empty_permission_without_fallback(self):
        instructions = ("每天都不改", "每天都不要修改", "所有日期保持不变", "第一天不改，第二天不改，第三天不改", "只改第二天，第二天保持原样")
        for instruction in instructions:
            for target in ("day-2", "all"):
                with self.subTest(instruction=instruction, target=target):
                    self.assertEqual(guard.requested_regions(instruction, target), set())
                    with self.assertRaises(HTTPException):
                        guard.freeze_scope(self.snapshot(), self.scope(["day-2"]), instruction, target)

    def test_whole_day_change_and_preserve_phrases_cannot_fall_back_to_selection(self):
        instructions = (
            "每天都不改动", "每天都不要改动", "所有日期都保留", "全部日程保留原样",
            "第一天不改动，第二天不要改动，第三天都保留",
        )
        for instruction in instructions:
            for target in ("day-2", "all"):
                with self.subTest(instruction=instruction, target=target):
                    self.assertEqual(guard.requested_regions(instruction, target), set())
                    with self.assertRaises(HTTPException):
                        guard.freeze_scope(self.snapshot(), self.scope(["day-2"]), instruction, target)
        for suffix in ("不改动", "不要改动", "都保留"):
            instruction = "每天加地图，第一天" + suffix
            with self.subTest(instruction=instruction):
                self.assertEqual(guard.requested_regions(instruction, "all"), {"day-2", "day-3"})
                frozen = guard.freeze_scope(self.snapshot(), self.scope(["day-2", "day-3"]), instruction, "all")
                self.assertEqual({permission.regionId for permission in frozen.permissions}, {"day-2", "day-3"})

    def test_preserving_a_named_field_with_these_phrases_does_not_protect_the_whole_day(self):
        for instruction in ("第二天景点不改动，添加地图", "第二天景点不要改动，添加地图", "第二天的景点都保留，添加地图"):
            for target in ("day-1", "all"):
                with self.subTest(instruction=instruction, target=target):
                    self.assertEqual(guard.requested_regions(instruction, target), {"day-2"})
                    frozen = guard.freeze_scope(self.snapshot(), self.scope(["day-2"]), instruction, target)
                    self.assertEqual([permission.regionId for permission in frozen.permissions], ["day-2"])
        for suffix in ("不改动", "不要改动", "都保留"):
            instruction = "每天加地图，第一天的景点" + suffix
            with self.subTest(instruction=instruction):
                self.assertEqual(guard.requested_regions(instruction, "all"), set(guard.REGIONS))

    def test_all_protected_request_fails_before_any_paid_model_call(self):
        with tempfile.TemporaryDirectory(prefix="travel-scope-import-") as temporary:
            with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": temporary}):
                module = importlib.import_module("backend.app")
            with patch.object(module, "get_settings", return_value=Settings(agent_mode="llm", llm_api_key="local-fixture")), patch.object(agent, "scope_request", side_effect=AssertionError("must not call model")) as scope_model, patch.object(agent, "candidates_request", side_effect=AssertionError("must not call model")) as candidate_model:
                session = {**module.initial_payload(), "version": 1, "history": []}
                with self.assertRaises(HTTPException) as failure:
                    module.build_proposal(session, module.GenerateBody(baseVersion=1, targetRegionId="all", instruction="每天都不要修改"))
                self.assertEqual(failure.exception.status_code, 400)
                scope_model.assert_not_called()
                candidate_model.assert_not_called()

    def test_english_whole_day_exception_and_date_snapshot(self):
        self.assertEqual(guard.requested_regions("Add a map for every day, but keep day 1 unchanged", "all"), {"day-2", "day-3"})
        self.assertEqual(guard.requested_regions("只改2026-10-27，添加地图", "all", self.snapshot()), {"day-2"})


if __name__ == "__main__":
    unittest.main()
