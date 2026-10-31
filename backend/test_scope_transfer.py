"""Dynamic date and presentation transfer boundaries; no model or map calls."""
from copy import deepcopy
import unittest

from fastapi import HTTPException

from backend import agent
from backend import scope as guard
from backend.catalog import PLACES, HOTELS
from backend.template_transfer import canonical_dates, copy_style_updates, parse_style_transfer, transfer_scope


def snapshot(count=3):
    days = {f"day{n}": {"date": f"2026-11-{n:02}", "title": f"第{n}天的独有标题", "note": f"独有备注{n}", "stops": [{"id": f"day{n}-stop-1", "placeId": PLACES[n - 1]["id"], "startTime": f"{8 + n:02}:30"}]} for n in range(1, count + 1)}
    return {
        "version": 1,
        "tripState": {"city": "杭州", "budgetLimit": 2600, "hotelId": HOTELS[0]["id"], "nights": count - 1, "days": days},
        "uiSpec": {"regions": {f"day-{n}": {"id": f"day-{n}", "binding": f"days.day{n}", "view": "cards", "showCost": True, "showAlternatives": False, "compact": False, "showTransport": False, "showMap": False, "transportMode": "walking" if n > 1 else "transit", "mapPlacement": "afterStops", "transportStyle": "inline", "blocks": [], "surface": None, "surfaceState": {"checked": [f"own-{n}"]}} for n in range(1, count + 1)}},
        "controlState": {"lockedRegions": [], "hotelLocked": False}, "routeData": {}, "catalog": {"places": deepcopy(PLACES), "hotels": deepcopy(HOTELS)},
    }


def surface(**changes):
    return {"title": "第一天页面", "html": '<section id="day1"><h2 id="heading"></h2><ol id="stops"></ol></section>', "css": "#day1 {padding:12px;color:#325843}", "javascript": "const heading=document.querySelector('#heading');window.travel.onChange(()=>{heading.textContent=window.travel.day.title;});", "placement": "replaceStops", **changes}


class ScopeTransferTests(unittest.TestCase):
    def test_source_date_is_read_only_and_explicit_destinations_override_dropdown(self):
        value = snapshot()
        before = deepcopy(value)
        instruction = "我想将单独修改后第一天的页面应用于第二天和第三天"
        expected = {"sourceRegionId": "day-1", "targetRegionIds": ["day-2", "day-3"]}
        self.assertEqual(parse_style_transfer(instruction, value), expected)
        for target in ("day-1", "day-2", "all"):
            self.assertEqual(guard.requested_regions(instruction, target, value), {"day-2", "day-3"})
        self.assertEqual(value, before)

    def test_reverse_shorthand_english_and_remaining_days(self):
        for instruction in ("第二天和第三天采用第一天的页面样式", "把第一天的样式复制到第二、三天", "Apply day 1 layout to day 2 and day 3", "将第一天模板应用到其他日期"):
            with self.subTest(instruction=instruction):
                self.assertEqual(parse_style_transfer(instruction, snapshot()), {"sourceRegionId": "day-1", "targetRegionIds": ["day-2", "day-3"]})
        self.assertEqual(parse_style_transfer("把第一天的样式应用到其他日期，第二天不改", snapshot())["targetRegionIds"], ["day-3"])

    def test_dynamic_ranges_last_day_and_iso_date(self):
        value = snapshot(14)
        self.assertEqual(guard.requested_regions("第四至第六天加地图", "day-1", value), {"day-4", "day-5", "day-6"})
        self.assertEqual(guard.requested_regions("最后一天增加清单", "day-1", value), {"day-14"})
        self.assertEqual(guard.requested_regions("只改2026-11-11，添加地图", "all", value), {"day-11"})
        self.assertEqual(guard.requested_regions("每天加地图，第十三天不改", "all", value), set(value["uiSpec"]["regions"]) - {"day-13"})
        self.assertEqual(parse_style_transfer("将第一天的界面应用于第十一天和第十四天", value)["targetRegionIds"], ["day-11", "day-14"])

    def test_nonexistent_named_date_never_falls_back_to_dropdown(self):
        with self.assertRaises(HTTPException) as failure:
            guard.requested_regions("第四天加地图", "day-2", snapshot())
        self.assertEqual(failure.exception.status_code, 400)

    def test_copying_style_does_not_claim_combined_business_data_request(self):
        self.assertIsNone(parse_style_transfer("将第一天页面应用第二天，并将第三天时间改为10:00", snapshot()))
        self.assertIsNone(parse_style_transfer("第一天加地图，第二天改成时间轴", snapshot()))

    def test_mixed_copy_and_other_ui_changes_use_agent_with_source_read_only(self):
        instruction = "将第一天样式应用到第二天，并将第三天改成时间轴"
        self.assertIsNone(parse_style_transfer(instruction, snapshot()))
        self.assertEqual(guard.requested_regions(instruction, "day-1", snapshot()), {"day-2", "day-3"})
        self.assertIsNone(parse_style_transfer("将第一天样式应用第二天，隐藏费用", snapshot()))
        self.assertEqual(guard.requested_regions("将第一天样式应用第二天，并将第一天时间改为10:00", "day-3", snapshot()), {"day-1", "day-2"})

    def test_protected_date_mentions_do_not_become_copy_destinations(self):
        instruction = "将第一天页面应用于第三天，第二天景点和时间保持不变"
        self.assertEqual(parse_style_transfer(instruction, snapshot()), {"sourceRegionId": "day-1", "targetRegionIds": ["day-3"]})
        self.assertEqual(guard.requested_regions(instruction, "all", snapshot()), {"day-3"})
        self.assertEqual(parse_style_transfer("把第一天的样式复制到第二天,第三天", snapshot())["targetRegionIds"], ["day-2", "day-3"])

    def test_negated_transfer_never_authorizes_mentioned_source_or_target(self):
        instruction = "不要复制第一天页面到第二天，只把第三天改成时间轴"
        self.assertIsNone(parse_style_transfer(instruction, snapshot()))
        self.assertEqual(guard.requested_regions(instruction, "all", snapshot()), {"day-3"})
        self.assertEqual(guard.requested_regions("不要将第一天页面复制到第二天", "day-2", snapshot()), set())

    def test_target_value_mismatch_stays_rejected_for_ordinary_scope(self):
        bad = agent.EditScope(message="错误权限", permissions=[agent.RegionPermission(regionId="day-2", uiFields=["showCost"], requiredUI=agent.UIValues(view="timeline"))])
        with self.assertRaises(HTTPException) as failure:
            guard.freeze_scope(snapshot(), bad, "第二天改成时间轴", "day-2")
        self.assertIn("目标值与允许修改字段不一致", failure.exception.detail)

    def test_controlled_transfer_scope_has_exact_required_fields(self):
        value = snapshot()
        value["uiSpec"]["regions"]["day-1"]["view"] = "timeline"
        scope = transfer_scope(value, "day-1", ["day-2", "day-3"])
        frozen = guard.freeze_scope(value, scope, "把第一天的页面样式应用于第二天和第三天", "day-1")
        for permission in frozen.permissions:
            self.assertEqual(set(permission.requiredUI.model_dump(exclude_none=True)), set(permission.uiFields))
            self.assertNotIn("transportMode", permission.uiFields)
            self.assertFalse(permission.placeStopIds)
            self.assertFalse(permission.timeStopIds)
            self.assertFalse(permission.editNote)

    def test_copy_rebinds_generated_ui_and_preserves_all_business_content(self):
        value = snapshot()
        source = value["uiSpec"]["regions"]["day-1"]
        source.update(view="timeline", compact=True, showCost=False, surface=surface())
        value["uiSpec"]["regions"]["day-2"]["blocks"] = [{"id": "own", "title": "自己的内容", "type": "note", "text": "不能覆盖", "items": [], "columns": [], "rows": []}]
        value["routeData"]["day-2"] = {"status": "ready", "message": "自己的路线"}
        before = deepcopy(value)
        updates = copy_style_updates(value, "day-1", ["day-2", "day-3"])
        self.assertEqual(value, before)
        self.assertEqual(len(updates), 2)
        for update in updates:
            region = update["regionId"]
            ui = update["nextUI"]
            self.assertEqual(set(update), {"regionId", "nextUI"})
            self.assertEqual(ui["binding"], "days." + region.replace("-", ""))
            self.assertEqual(ui["id"], region)
            self.assertEqual(ui["view"], "timeline")
            self.assertEqual(ui["transportMode"], before["uiSpec"]["regions"][region]["transportMode"])
            self.assertEqual(ui["blocks"], before["uiSpec"]["regions"][region]["blocks"])
            self.assertEqual(ui["surfaceState"], before["uiSpec"]["regions"][region]["surfaceState"])
            self.assertIn("window.travel.day.title", ui["surface"]["javascript"])
            self.assertNotIn("第一天", ui["surface"]["title"])

    def test_source_specific_static_content_requires_agent_adaptation(self):
        value = snapshot()
        source_day = value["tripState"]["days"]["day1"]
        for literal in (source_day["date"], source_day["title"], source_day["note"], source_day["stops"][0]["id"], source_day["stops"][0]["startTime"], PLACES[0]["name"]):
            with self.subTest(literal=literal):
                value["uiSpec"]["regions"]["day-1"]["surface"] = surface(html="<section>" + literal + "</section>")
                with self.assertRaises(HTTPException) as failure:
                    copy_style_updates(value, "day-1", ["day-2"])
                self.assertEqual(failure.exception.status_code, 422)

    def test_fixed_javascript_source_day_requires_adaptation(self):
        for expression in ("window.travel.trip.days.day1", "window.travel.trip.days['day1']"):
            value = snapshot()
            value["uiSpec"]["regions"]["day-1"]["surface"] = surface(javascript=f"console.log({expression});")
            with self.assertRaises(HTTPException) as failure:
                copy_style_updates(value, "day-1", ["day-2"])
            self.assertEqual(failure.exception.status_code, 422)

    def test_generic_css_ids_and_first_step_text_are_not_source_data(self):
        value = snapshot()
        value["uiSpec"]["regions"]["day-1"]["surface"] = surface(html='<section id="day1"><h2>第一步：查看当前行程</h2></section>')
        updates = copy_style_updates(value, "day-1", ["day-2"])
        self.assertEqual(updates[0]["nextUI"]["surface"]["css"], surface()["css"])

    def test_css_generated_source_label_requires_adaptation(self):
        value = snapshot()
        value["uiSpec"]["regions"]["day-1"]["surface"] = surface(css="section::before{content:'第一天'}")
        with self.assertRaises(HTTPException) as failure:
            copy_style_updates(value, "day-1", ["day-2"])
        self.assertEqual(failure.exception.status_code, 422)

    def test_target_lock_is_respected_but_source_can_be_read_while_locked(self):
        value = snapshot()
        value["uiSpec"]["regions"]["day-1"]["view"] = "timeline"
        value["controlState"]["lockedRegions"] = ["day-1"]
        self.assertEqual(len(copy_style_updates(value, "day-1", ["day-2"])), 1)
        value["controlState"]["lockedRegions"].append("day-2")
        with self.assertRaises(HTTPException) as failure:
            copy_style_updates(value, "day-1", ["day-2", "day-3"])
        self.assertEqual(failure.exception.status_code, 423)

    def test_legacy_content_blocks_need_adaptation_without_losing_target_blocks(self):
        value = snapshot()
        block = {"id": "source", "title": "行程说明", "type": "note", "text": "第一天有自己的数据", "items": [], "columns": [], "rows": []}
        value["uiSpec"]["regions"]["day-1"]["blocks"] = [block]
        self.assertEqual(transfer_scope(value, "day-1", ["day-2"]).permissions[0].newBlockTypes, ["note"])
        with self.assertRaises(HTTPException) as failure:
            copy_style_updates(value, "day-1", ["day-2"])
        self.assertEqual(failure.exception.status_code, 422)

    def test_already_matching_or_invalid_targets_are_explicit(self):
        value = snapshot()
        for targets in (["day-1"], ["day-99"], []):
            with self.subTest(targets=targets), self.assertRaises(HTTPException):
                copy_style_updates(value, "day-1", targets)
        # Ignore existing identity/state: when presentation is equal, no rewrite.
        with self.assertRaises(HTTPException) as failure:
            copy_style_updates(value, "day-1", ["day-2"])
        self.assertEqual(failure.exception.status_code, 400)

    def test_dynamic_updates_and_human_summaries_cover_fourteen_days(self):
        value = snapshot(14)
        value["uiSpec"]["regions"]["day-1"]["view"] = "timeline"
        scope = transfer_scope(value, "day-1", ["day-4", "day-14"])
        frozen = guard.freeze_scope(value, scope, "将第一天页面应用于第四天和第十四天", "all")
        updates = copy_style_updates(value, "day-1", ["day-4", "day-14"])
        guard.validate_updates(value, frozen, updates)
        self.assertTrue(any("第十四天" in label for label in guard.scope_summary(frozen)))
        self.assertTrue(any("第十三天" in label for label in guard.preserved_summary(frozen, value)))
        self.assertTrue(any("第十四天" in label for label in guard.describe_changes(value, updates, {})))
        guard.validate_operations(value, frozen, [agent.SetUI(kind="set_ui", regionId="day-14", view="timeline")])


if __name__ == "__main__":
    unittest.main()