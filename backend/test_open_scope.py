"""Scope regressions for open generated UI, independent of model/map services."""
from copy import deepcopy
import unittest

from fastapi import HTTPException
from backend import agent
from backend import scope as guard


class OpenScopeTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "version": 1,
            "tripState": {
                "city": "杭州", "hotelId": "lake-hotel", "budgetLimit": 2600, "nights": 2,
                "days": {key: {"title": key, "date": f"2026-11-0{index}", "note": "保留备注",
                    "stops": [{"id": f"{key}-s1", "placeId": "west-lake", "startTime": "09:30"},
                              {"id": f"{key}-s2", "placeId": "art-space", "startTime": "13:30"}]}
                         for index, key in enumerate(guard.REGIONS.values(), 1)},
            },
            "uiSpec": {"regions": {region: {"view": "cards", "showMap": False, "showTransport": False,
                         "transportMode": "transit", "blocks": [], "surface": None,
                         "surfaceState": {"checked": ["raincoat"]}} for region in guard.REGIONS}},
            "controlState": {"lockedRegions": [], "hotelLocked": False}, "routeData": {},
        }

    def permission(self, **values):
        return agent.RegionPermission(regionId="day-2", **values)

    def scope(self, **values):
        return agent.EditScope(message="只改明确请求的内容", permissions=[self.permission(**values)])

    def update(self):
        return {"regionId": "day-2", "nextUI": deepcopy(self.snapshot["uiSpec"]["regions"]["day-2"])}

    def surface(self):
        return {"title": "可筛选景点", "html": '<input id="query"><ul id="result"></ul>',
                "css": "input{width:100%}", "javascript": "document.querySelector('#query').value = '';",
                "placement": "afterStops"}

    def rejected(self, callback):
        with self.assertRaises(HTTPException):
            callback()

    def test_open_surface_can_be_frozen_without_old_ui_menu_permissions(self):
        frozen = guard.freeze_scope(self.snapshot, self.scope(editSurface=True), "第二天添加景点筛选器", "all")
        self.assertTrue(frozen.permissions[0].editSurface)
        self.assertEqual(frozen.permissions[0].uiFields, [])
        self.assertIn("Agent生成的界面与交互", guard.scope_summary(frozen)[0])

    def test_surface_authorization_does_not_allow_other_ui_fields_or_data(self):
        scope = self.scope(editSurface=True)
        update = self.update()
        update["nextUI"]["surface"] = self.surface()
        guard.validate_updates(self.snapshot, scope, [update])
        update["nextUI"]["showMap"] = True
        self.rejected(lambda: guard.validate_updates(self.snapshot, scope, [update]))
        update = self.update()
        update["nextData"] = deepcopy(self.snapshot["tripState"]["days"]["day2"])
        update["nextData"]["note"] = "不应覆盖"
        self.rejected(lambda: guard.validate_updates(self.snapshot, scope, [update]))

    def test_model_cannot_reset_saved_widget_state_even_when_editing_surface(self):
        update = self.update()
        update["nextUI"]["surface"] = self.surface()
        update["nextUI"]["surfaceState"] = {}
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update]))

    def test_surface_operation_requires_specific_permission(self):
        operation = agent.SetSurface(kind="set_surface", regionId="day-2", surface=self.surface())
        self.rejected(lambda: guard.validate_operations(self.snapshot, self.scope(uiFields=["view"]), [operation]))
        guard.validate_operations(self.snapshot, self.scope(editSurface=True), [operation])
        remove = agent.SetSurface(kind="set_surface", regionId="day-2", surface=None)
        guard.validate_operations(self.snapshot, self.scope(editSurface=True), [remove])

    def test_append_component_request_cannot_replace_existing_stops_presentation(self):
        surface = self.surface()
        surface["placement"] = "replaceStops"
        scope = self.scope(editSurface=True)
        operation = agent.SetSurface(kind="set_surface", regionId="day-2", surface=surface)
        self.rejected(lambda: guard.validate_operations(self.snapshot, scope, [operation]))
        update = self.update()
        update["nextUI"]["surface"] = surface
        self.rejected(lambda: guard.validate_updates(self.snapshot, scope, [update]))
        replacement_scope = self.scope(editSurface=True, replacePresentation=True)
        guard.validate_operations(self.snapshot, replacement_scope, [operation])
        guard.validate_updates(self.snapshot, replacement_scope, [update])

    def test_existing_generated_replacement_can_be_edited_without_expanding_placement(self):
        surface = self.surface()
        surface["placement"] = "replaceStops"
        self.snapshot["uiSpec"]["regions"]["day-2"]["surface"] = surface
        changed = {**surface, "css": "input{color:green}"}
        update = self.update()
        update["nextUI"]["surface"] = changed
        guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update])

    def test_replace_presentation_flag_alone_cannot_grant_surface_rights(self):
        self.rejected(lambda: guard.freeze_scope(self.snapshot, self.scope(replacePresentation=True), "第二天重排布局", "day-2"))

    def test_legacy_region_can_acquire_surface_but_not_unknown_fields(self):
        del self.snapshot["uiSpec"]["regions"]["day-2"]["surface"]
        update = self.update()
        update["nextUI"]["surface"] = self.surface()
        guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update])
        update["nextUI"]["unknownUiField"] = 1
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update]))

    def test_trip_only_scope_grants_no_day_rights(self):
        scope = agent.EditScope(message="只改预算", permissions=[], tripFields=["budgetLimit"])
        frozen = guard.freeze_scope(self.snapshot, scope, "所有日期保持不变，只把预算改成3000", "all")
        self.assertEqual(frozen.permissions, [])
        guard.validate_trip_changes(self.snapshot, frozen, {"budgetLimit": 3000})
        guard.validate_updates(self.snapshot, frozen, [])
        update = self.update()
        update["nextUI"]["view"] = "timeline"
        self.rejected(lambda: guard.validate_updates(self.snapshot, frozen, [update]))
        preserved = "；".join(guard.preserved_summary(frozen))
        self.assertNotIn("总预算", preserved)
        self.assertIn("酒店选择", preserved)

    def test_trip_fields_cannot_be_widened_by_candidate(self):
        scope = agent.EditScope(message="改预算", tripFields=["budgetLimit"])
        self.rejected(lambda: guard.validate_trip_changes(self.snapshot, scope, {"hotelId": "other-hotel"}))
        self.rejected(lambda: guard.validate_trip_changes(self.snapshot, scope, {"city": "北京"}))
        guard.validate_trip_changes(self.snapshot, scope, {"budgetLimit": 3000, "hotelId": "lake-hotel"})

    def test_trip_field_values_are_checked_before_application(self):
        scope = agent.EditScope(message="调整预算与住宿", tripFields=["budgetLimit", "nights"])
        for changes in ({"budgetLimit": True}, {"budgetLimit": float("nan")}, {"budgetLimit": 20},
                        {"budgetLimit": 100001}, {"nights": True}, {"nights": -1}, {"nights": 2.5}):
            with self.subTest(changes=changes):
                self.rejected(lambda: guard.validate_trip_changes(self.snapshot, scope, changes))
        guard.validate_trip_changes(self.snapshot, scope, {"budgetLimit": 3999.5, "nights": 3})

    def test_hotel_lock_blocks_hotel_and_nights_but_allows_budget(self):
        self.snapshot["controlState"]["hotelLocked"] = True
        for field, value in (("hotelId", "other-hotel"), ("nights", 3)):
            scope = agent.EditScope(message="修改住宿", tripFields=[field])
            self.rejected(lambda: guard.freeze_scope(self.snapshot, scope, "修改住宿", "day-2"))
            self.rejected(lambda: guard.validate_trip_changes(self.snapshot, scope, {field: value}))
        scope = agent.EditScope(message="只改预算", tripFields=["budgetLimit"])
        guard.freeze_scope(self.snapshot, scope, "预算改成3000", "day-2")
        guard.validate_trip_changes(self.snapshot, scope, {"budgetLimit": 3000})

    def test_day_title_and_date_need_independent_permissions(self):
        before = self.snapshot["tripState"]["days"]["day2"]
        update = self.update()
        update["nextData"] = {**deepcopy(before), "title": "轻松出游"}
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update]))
        guard.validate_updates(self.snapshot, self.scope(editTitle=True), [update])
        update["nextData"]["date"] = "2026-11-07"
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editTitle=True), [update]))
        guard.validate_updates(self.snapshot, self.scope(editTitle=True, editDate=True), [update])

    def test_set_day_operation_cannot_bypass_time_or_poi_rights(self):
        stops = deepcopy(self.snapshot["tripState"]["days"]["day2"]["stops"])
        stops[0]["startTime"] = "10:30"
        operation = agent.SetDay(kind="set_day", regionId="day-2", stops=stops)
        self.rejected(lambda: guard.validate_operations(self.snapshot, self.scope(editStructure=True), [operation]))
        guard.validate_operations(self.snapshot, self.scope(timeStopIds=["day2-s1"]), [operation])
        stops[0]["placeId"] = "tea-house"
        operation = agent.SetDay(kind="set_day", regionId="day-2", stops=stops)
        self.rejected(lambda: guard.validate_operations(self.snapshot, self.scope(editStructure=True, timeStopIds=["day2-s1"]), [operation]))

    def test_structure_permission_allows_reordering_without_mutating_existing_fields(self):
        update = self.update()
        day = deepcopy(self.snapshot["tripState"]["days"]["day2"])
        day["stops"].reverse()
        update["nextData"] = day
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update]))
        guard.validate_updates(self.snapshot, self.scope(editStructure=True), [update])
        day["stops"][0]["startTime"] = "17:00"
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editStructure=True), [update]))

    def test_structure_permission_allows_add_and_remove_but_preserves_other_regions_ids(self):
        update = self.update()
        day = deepcopy(self.snapshot["tripState"]["days"]["day2"])
        day["stops"] = [day["stops"][0], {"id": "day2-new", "placeId": "tea-house", "startTime": "16:00"}]
        update["nextData"] = day
        guard.validate_updates(self.snapshot, self.scope(editStructure=True), [update])
        day["stops"][1]["id"] = "day1-s1"
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editStructure=True), [update]))

    def test_generated_surface_cannot_mutate_routes_without_route_data_permission(self):
        update = self.update()
        update["nextUI"]["surface"] = self.surface()
        update["routeData"] = {"status": "ready"}
        self.rejected(lambda: guard.validate_updates(self.snapshot, self.scope(editSurface=True), [update]))
        guard.validate_updates(self.snapshot, self.scope(editSurface=True, editStructure=True), [update])

    def test_missing_requested_open_surface_date_is_rejected(self):
        scope = agent.EditScope(message="每天添加筛选器", permissions=[agent.RegionPermission(regionId=region, editSurface=True) for region in guard.REGIONS])
        self.rejected(lambda: guard.validate_updates(self.snapshot, scope, [self.update()]))

    def test_changes_describe_reorder_by_stable_ids_and_new_surface(self):
        update = self.update()
        update["nextUI"]["surface"] = self.surface()
        day = deepcopy(self.snapshot["tripState"]["days"]["day2"])
        day["stops"].reverse()
        update["nextData"] = day
        places = {"west-lake": {"name": "西湖"}, "art-space": {"name": "美术馆"}}
        changes = guard.describe_changes(self.snapshot, [update], places)
        self.assertTrue(any("生成的界面" in item for item in changes))
        self.assertTrue(any("调整既有景点顺序" in item for item in changes))
        self.assertFalse(any("改为西湖" in item or "改为美术馆" in item for item in changes))


if __name__ == "__main__":
    unittest.main()
