"""Phase 1 guard tests: pure fixtures, no database/model/map/production writes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch as mock_patch

from fastapi import HTTPException

from backend.catalog import PLACES, HOTELS
from backend.trip_document import (
    activity_id, component_registry, day_id, normalize_editor_state,
    set_component_lock, to_trip_document, validate_document_schema, validate_locked_unchanged,
)
from backend.patch_guard import decode_pointer, leaf_diff, resolve_scope, validate_patch


def fixture():
    days = {}
    for number in (1, 2, 3):
        key = "day" + str(number)
        days[key] = {
            "date": f"2026-11-{number:02}", "title": f"测试第{number}天", "note": f"原备注{number}",
            "stops": [
                {"id": key + "-persisted-a", "placeId": PLACES[(number - 1) * 3]["id"], "startTime": "09:30"},
                {"id": key + "-persisted-b", "placeId": PLACES[(number - 1) * 3 + 1]["id"], "startTime": "13:30"},
                {"id": key + "-persisted-c", "placeId": PLACES[(number - 1) * 3 + 2]["id"], "startTime": "16:00"},
            ],
        }
    value = {
        "id": "phase-one-isolated-test", "version": 7,
        "tripState": {"city": "杭州", "budgetLimit": 2600, "hotelId": HOTELS[0]["id"], "nights": 2, "days": days},
        "uiSpec": {"regions": {f"day-{n}": {"view": "cards", "surface": None} for n in (1, 2, 3)}},
        "controlState": {"lockedRegions": [], "hotelLocked": False},
        "routeData": {"day-1": {"fixture": 1}, "day-2": {"fixture": 2}, "day-3": {"fixture": 3}},
        "catalog": {"places": deepcopy(PLACES), "hotels": deepcopy(HOTELS)},
        "_undo": [], "history": [],
    }
    value["_initialPayload"] = {"tripState": deepcopy(value["tripState"])}
    return value


class PatchGuardTests(unittest.TestCase):
    def setUp(self):
        self.session = normalize_editor_state(fixture())
        self.registry = component_registry(self.session)
        self.target = next(cid for cid, item in self.registry.items() if item.get("stopId") == "day2-persisted-b")
        self.aid = self.registry[self.target]["entityId"]
        self.pm = self.registry[self.target]["parentId"]
        self.path = f"/entities/activities/{self.aid}/description"

    def operation(self, path=None, value="缩短游览时长，增加休息"):
        return {"op": "replace", "path": path or self.path, "value": value}

    def assert_rejected(self, patch, selected=None, fields=None, code=None):
        original = deepcopy(self.session)
        with self.assertRaises(HTTPException) as raised:
            validate_patch(self.session, selected if selected is not None else [self.target], patch, fields)
        self.assertEqual(self.session, original, "Failed validation must not mutate any official/metadata field")
        if code:
            self.assertEqual(raised.exception.detail["code"], code)
        return raised.exception

    def test_authorised_description_changes_only_leaf_and_preserves_day_one_three(self):
        original = deepcopy(self.session)
        result = validate_patch(self.session, [self.pm], [self.operation()], ["description"])
        self.assertEqual(result["changedPaths"], [self.path])
        self.assertEqual(result["draft"]["entities"]["activities"][self.aid]["description"], "缩短游览时长，增加休息")
        self.assertEqual(result["payload"]["tripState"]["days"]["day1"], original["tripState"]["days"]["day1"])
        self.assertEqual(result["payload"]["tripState"]["days"]["day3"], original["tripState"]["days"]["day3"])
        self.assertEqual(self.session, original)
        self.assertNotIn("version", result["payload"])
        self.assertNotIn("_undo", result["payload"])
        self.assertTrue(all(result["validationReport"].values()))
        mixed = validate_patch(self.session, [self.target], [self.operation(), self.operation(self.path.replace("/description", "/startTime"), "13:30")], ["description", "startTime"])
        self.assertNotIn("routeData", mixed["payload"], "A time NOOP must not clear existing maps during a description edit")

    def test_authorised_time_updates_same_stable_id_and_invalidates_only_day_two_route(self):
        path = self.path.replace("/description", "/startTime")
        result = validate_patch(self.session, [self.target], [self.operation(path, "14:00")], ["startTime"])
        after = {**self.session, **result["payload"]}
        self.assertEqual(component_registry(after), self.registry)
        self.assertEqual(after["editorMetadata"], self.session["editorMetadata"])
        self.assertNotIn("day-2", after["routeData"])
        self.assertEqual(set(after["routeData"]), {"day-1", "day-3"})

    def test_source_ids_and_component_ids_survive_reorder(self):
        before = to_trip_document(self.session)
        changed = deepcopy(self.session)
        changed["tripState"]["days"] = dict(reversed(list(changed["tripState"]["days"].items())))
        changed["tripState"]["days"]["day2"]["stops"].reverse()
        after = to_trip_document(changed)
        self.assertEqual(set(before["entities"]["activities"]), set(after["entities"]["activities"]))
        self.assertEqual(before["ui"]["components"], after["ui"]["components"])
        self.assertEqual(component_registry(changed), self.registry)

    def test_ids_differ_between_trips_and_do_not_use_current_indices(self):
        changed = deepcopy(self.session)
        changed["id"] = "another-trip"
        self.assertNotEqual(day_id(changed, "day2"), day_id(self.session, "day2"))
        self.assertNotEqual(activity_id(changed, "day2", "day2-persisted-b"), self.aid)

    def test_membership_seeded_from_initial_snapshot_not_current_time(self):
        changed = fixture()
        changed["tripState"]["days"]["day2"]["stops"][1]["startTime"] = "11:00"
        normalized = normalize_editor_state(changed)
        self.assertEqual(normalized["editorMetadata"]["activityPeriods"][self.aid], "pm")
        checked = validate_patch(normalized, [self.target], [self.operation(self.path.replace("/description", "/startTime"), "11:00")], ["startTime"])
        self.assertEqual(checked["draft"]["entities"]["activities"][self.aid]["period"], "am")
        self.assertEqual(checked["payload"]["editorMetadata"]["activityPeriods"][self.aid], "am")
        self.assertNotIn("routeData", checked["payload"])
        self.assertEqual(normalized["editorMetadata"]["activityPeriods"][self.aid], "pm", "Preview cannot silently migrate stored metadata")

    def test_no_selection_is_not_global_authorisation(self):
        self.assert_rejected([self.operation()], [], code="NO_SELECTION")

    def test_unknown_or_multiple_components_refused(self):
        for selected in (["unknown"], [self.target, self.pm]):
            with self.subTest(selected=selected):
                self.assert_rejected([self.operation()], selected)

    def test_all_days_and_populated_periods_have_scoped_authorisation(self):
        for cid, item in self.registry.items():
            scope = resolve_scope(self.session, [cid])
            if item.get("period") == "evening":
                self.assertEqual(scope["allowedPaths"], [])
            else:
                self.assertTrue(scope["allowedPaths"], cid)
                for aid in scope["eligibleActivityIds"]:
                    binding = next(x for x in self.registry.values() if x["kind"] == "ActivityCard" and x["entityId"] == aid)
                    self.assertEqual(binding["dayKey"], item["dayKey"])
                    if item.get("period"):
                        self.assertEqual(binding["period"], item["period"])

    def test_whole_day_selection_includes_its_morning_and_afternoon_only(self):
        cid = next(cid for cid, item in self.registry.items() if item["kind"] == "DayCard" and item["dayKey"] == "day2")
        scope = resolve_scope(self.session, [cid])
        self.assertEqual(len(scope["eligibleActivityIds"]), 3)
        self.assertEqual(len(scope["allowedPaths"]), 6)

    def test_client_selected_fields_narrow_scope(self):
        self.assert_rejected([self.operation(self.path.replace("/description", "/startTime"), "14:00")], fields=["description"], code="PATCH_OUT_OF_SCOPE")
        for fields in (["title"], ["description", "title"], [], ["description", "description"], "description", 12, [{}]):
            with self.subTest(fields=fields):
                self.assert_rejected([self.operation()], fields=fields, code="INVALID_REQUEST")

    def test_locked_source_day_one_three_remain_unchanged_with_legal_target_patch(self):
        for day in ("day1", "day3"):
            cid = next(cid for cid, item in self.registry.items() if item["kind"] == "DayCard" and item["dayKey"] == day)
            self.session.update(set_component_lock(self.session, cid, True))
        before = deepcopy(self.session)
        result = validate_patch(self.session, [self.pm], [self.operation()])
        validate_locked_unchanged(self.session, {**self.session, **result["payload"]})
        self.assertEqual(self.session, before)

    def test_locked_day_period_or_activity_each_refuse_patch(self):
        targets = [self.target, self.pm, self.registry[self.pm]["parentId"]]
        for cid in targets:
            with self.subTest(component=cid):
                self.session = normalize_editor_state(fixture())
                self.session.update(set_component_lock(self.session, cid, True))
                self.assert_rejected([self.operation()], code="LOCKED_COMPONENT")

    def test_locked_descendant_is_rejected_when_parent_selected(self):
        self.session.update(set_component_lock(self.session, self.target, True))
        self.assert_rejected([self.operation()], [self.pm], code="LOCKED_COMPONENT")

    def test_legacy_generation_lock_refuses_patch_but_not_direct_user_change_guard(self):
        self.session["controlState"]["lockedRegions"] = ["day-2"]
        self.assert_rejected([self.operation()], code="LOCKED_COMPONENT")
        after = deepcopy(self.session)
        after["tripState"]["days"]["day2"]["note"] = "手工备注"
        validate_locked_unchanged(self.session, after)

    def test_new_component_lock_does_not_toggle_old_generation_lock(self):
        result = set_component_lock(self.session, self.registry[self.pm]["parentId"], True)
        self.assertEqual(result["controlState"], self.session["controlState"])

    def test_global_guard_blocks_legacy_routes_modifying_locked_day_or_activity(self):
        for cid in (self.target, self.registry[self.pm]["parentId"]):
            with self.subTest(component=cid):
                current = deepcopy(self.session)
                current.update(set_component_lock(current, cid, True))
                changed = deepcopy(current)
                changed["tripState"]["days"]["day2"]["stops"][1]["placeId"] = PLACES[0]["id"]
                with self.assertRaises(HTTPException):
                    validate_locked_unchanged(current, changed)

    def test_new_day_lock_protects_day_note_and_ui(self):
        self.session.update(set_component_lock(self.session, self.registry[self.pm]["parentId"], True))
        for mutation in ("note", "ui"):
            changed = deepcopy(self.session)
            if mutation == "note":
                changed["tripState"]["days"]["day2"]["note"] = "被禁止的备注"
            else:
                changed["uiSpec"]["regions"]["day-2"]["view"] = "timeline"
            with self.assertRaises(HTTPException):
                validate_locked_unchanged(self.session, changed)

    def test_constraint_changes_cannot_hide_lock_from_global_guard(self):
        self.session.update(set_component_lock(self.session, self.target, True))
        changed = deepcopy(self.session)
        changed["persistedConstraints"]["lockedComponentIds"] = []
        changed["tripState"]["days"]["day2"]["stops"][1]["startTime"] = "14:00"
        with self.assertRaises(HTTPException):
            validate_locked_unchanged(self.session, changed)

    def test_other_day_write_refused_even_when_in_same_batch_as_valid_write(self):
        aid = activity_id(self.session, "day1", "day1-persisted-b")
        self.assert_rejected([self.operation(), self.operation(f"/entities/activities/{aid}/description")], code="PATCH_OUT_OF_SCOPE")

    def test_path_prefix_collision_not_authorised(self):
        for path in (self.path + "Extra", self.path.replace(self.aid, self.aid + "0"), self.path + "/child"):
            with self.subTest(path=path):
                self.assert_rejected([self.operation(path)], code="PATCH_OUT_OF_SCOPE")

    def test_parent_overwrite_rejected(self):
        for path in ("/entities", "/entities/activities", "/entities/activities/" + self.aid):
            with self.subTest(path=path):
                self.assert_rejected([self.operation(path, {"description": "恶意覆盖"})])

    def test_internal_metadata_and_ui_rejected(self):
        for path in ("/version", "/schemaVersion", "/tripId", "/dayOrder", "/ui", "/persistedConstraints", "/editorMetadata", "/_owner"):
            with self.subTest(path=path):
                self.assert_rejected([self.operation(path, 8)])

    def test_valid_special_pointer_decoding_is_single_pass(self):
        self.assertEqual(decode_pointer("/a~1b/c~0d/e~01f"), ("a/b", "c~d", "e~1f"))
        self.assert_rejected([self.operation(self.path.replace("description", "~1description"))], code="PATCH_OUT_OF_SCOPE")
        self.assert_rejected([self.operation(self.path.replace("description", "~01description"))], code="PATCH_OUT_OF_SCOPE")

    def test_invalid_and_special_pointer_rejected(self):
        for path in ("", "/", "entities/activities", "#/entities", "/entities//activities", "/entities/~2", "/entities/%2Factivities", "/entities/../activities", "/entities/-", "/entities/\x00"):
            with self.subTest(path=path):
                self.assert_rejected([self.operation(path if path else "/")])

    def test_prototype_pollution_property_names_rejected(self):
        for key in ("__proto__", "prototype", "constructor", "__class__", "_internal"):
            with self.subTest(key=key):
                self.assert_rejected([self.operation("/entities/" + key + "/description")], code="PATCH_OUT_OF_SCOPE")
                self.assert_rejected([self.operation(value={key: {}})], code="PATCH_OUT_OF_SCOPE")

    def test_wrong_patch_operations_and_shapes(self):
        for patch in ({}, [], None, ["replace"], [{"op": "remove", "path": self.path}], [{"op": "replace", "path": self.path}], [{"op": "replace", "path": self.path, "value": "x", "from": self.path}]):
            with self.subTest(patch=patch):
                self.assert_rejected(patch, code="INVALID_REQUEST")
        for operation in ("add", "remove", "move", "copy", "test"):
            self.assert_rejected([{**self.operation(), "op": operation}], code="INVALID_REQUEST")

    def test_non_finite_or_wrong_type_value_refused(self):
        for value in (float("nan"), float("inf"), 2, True, None, [], {"description": "object"}):
            with self.subTest(value=value):
                self.assert_rejected([self.operation(value=value)])

    def test_patch_limits_duplicate_paths_and_text_length(self):
        self.assert_rejected([self.operation()] * 17, code="INVALID_REQUEST")
        self.assert_rejected([self.operation(), self.operation(value="second")], code="INVALID_REQUEST")
        self.assert_rejected([self.operation(value="x" * 2001)], code="SCHEMA_INVALID")
        self.assert_rejected([self.operation(value="x" * 16001)], code="INVALID_REQUEST")

    def test_noop_patch_does_not_create_business_change(self):
        old = to_trip_document(self.session)["entities"]["activities"][self.aid]["description"]
        self.assert_rejected([self.operation(value=old)], code="NO_CHANGE")

    def test_invalid_time_or_time_order_cannot_modify_formal_state(self):
        path = self.path.replace("/description", "/startTime")
        for value in ("25:00", "2:00", "14:99", "09:00", "18:30", "16:00", "17:00"):
            with self.subTest(value=value):
                self.assert_rejected([self.operation(path, value)], fields=["startTime"])

    def test_schema_rejects_extra_metadata_or_duplicate_activity_bindings(self):
        document = to_trip_document(self.session)
        document["entities"]["activities"][self.aid]["unexpected"] = True
        with self.assertRaises(HTTPException):
            validate_document_schema(document)
        document = to_trip_document(self.session)
        did = document["entities"]["activities"][self.aid]["dayId"]
        document["entities"]["days"][did]["activityIds"].append(self.aid)
        with self.assertRaises(HTTPException):
            validate_document_schema(document)

    def test_adapter_rejects_duplicate_persisted_stop_ids(self):
        self.session["tripState"]["days"]["day2"]["stops"][1]["id"] = "day2-persisted-a"
        with self.assertRaises(HTTPException):
            to_trip_document(self.session)

    def test_projection_defect_cannot_hide_changes_to_legacy_notes(self):
        from backend import patch_guard as guard
        original_project = guard.project_back
        def defective(*args):
            payload = original_project(*args)
            payload["tripState"]["days"]["day1"]["note"] = "Hidden unauthorised change"
            return payload
        with mock_patch.object(guard, "project_back", side_effect=defective):
            self.assert_rejected([self.operation()], code="PATCH_OUT_OF_SCOPE")

    def test_deeply_nested_value_is_rejected_cleanly(self):
        value = "x"
        for _ in range(12):
            value = [value]
        self.assert_rejected([self.operation(value=value)], code="INVALID_REQUEST")

    def test_leaf_diff_marks_exact_actual_changes(self):
        before = to_trip_document(self.session)
        after = deepcopy(before)
        after["entities"]["activities"][self.aid]["description"] = "叶子差异"
        self.assertEqual(leaf_diff(before, after), [self.path])


    def test_cross_period_time_derives_parent_metadata_but_preserves_stable_id(self):
        path = self.path.replace("/description", "/startTime")
        original = deepcopy(self.session)
        checked = validate_patch(self.session, [self.target], [self.operation(path, "11:00")], ["startTime"])
        draft = checked["draft"]
        self.assertEqual(draft["entities"]["activities"][self.aid]["period"], "am")
        self.assertEqual(draft["ui"]["components"][self.target]["parentId"], self.pm.removesuffix("pm") + "am")
        self.assertEqual(set(checked["derivedChangedPaths"]), {f"/entities/activities/{self.aid}/period", f"/ui/components/{self.target}/parentId"})
        self.assertEqual(checked["payload"]["editorMetadata"]["activityPeriods"][self.aid], "am")
        self.assertEqual(set(draft["entities"]["activities"]), set(to_trip_document(self.session)["entities"]["activities"]))
        self.assertEqual(self.session, original)

    def test_moving_into_locked_period_is_rejected_atomically(self):
        am = self.pm.removesuffix("pm") + "am"
        self.session.update(set_component_lock(self.session, am, True))
        self.assert_rejected([self.operation(self.path.replace("/description", "/startTime"), "11:00")], fields=["startTime"], code="LOCKED_COMPONENT")

    def test_place_change_derives_catalog_title_and_only_missing_description(self):
        path = self.path.replace("/description", "/placeId")
        used = {x["placeId"] for x in self.session["tripState"]["days"]["day2"]["stops"]}
        place = next(p for p in PLACES if p["id"] not in used)
        checked = validate_patch(self.session, [self.target], [self.operation(path, place["id"])], ["placeId"])
        item = checked["draft"]["entities"]["activities"][self.aid]
        self.assertEqual(item["title"], place["name"])
        self.assertEqual(item["description"], place["description"])
        self.assertIn(f"/entities/activities/{self.aid}/title", checked["derivedChangedPaths"])
        self.assertNotIn("description", checked["payload"]["tripState"]["days"]["day2"]["stops"][1])
        self.assertNotIn("day-2", checked["payload"]["routeData"])
        self.session["tripState"]["days"]["day2"]["stops"][1]["description"] = "用户自己的说明"
        checked = validate_patch(self.session, [self.target], [self.operation(path, place["id"])], ["placeId"])
        self.assertEqual(checked["draft"]["entities"]["activities"][self.aid]["description"], "用户自己的说明")

    def test_place_change_can_explicitly_preserve_implicit_description(self):
        path = self.path.replace("/description", "/placeId")
        used = {x["placeId"] for x in self.session["tripState"]["days"]["day2"]["stops"]}
        place = next(p for p in PLACES if p["id"] not in used)
        old = to_trip_document(self.session)["entities"]["activities"][self.aid]["description"]
        checked = validate_patch(self.session, [self.target], [self.operation(path, place["id"]), self.operation(value=old)], ["description", "placeId"])
        self.assertEqual(checked["draft"]["entities"]["activities"][self.aid]["description"], old)
        self.assertEqual(checked["payload"]["tripState"]["days"]["day2"]["stops"][1]["description"], old)

    def test_fabricated_duplicate_or_unauthorised_place_is_rejected(self):
        path = self.path.replace("/description", "/placeId")
        self.assert_rejected([self.operation(path, "not-a-real-poi")], fields=["placeId"], code="SCHEMA_INVALID")
        other = self.session["tripState"]["days"]["day2"]["stops"][0]["placeId"]
        self.assert_rejected([self.operation(path, other)], fields=["placeId"], code="INVARIANT_VIOLATION")
        self.assert_rejected([self.operation(path, other)], fields=["description"], code="PATCH_OUT_OF_SCOPE")

if __name__ == "__main__":
    unittest.main()
