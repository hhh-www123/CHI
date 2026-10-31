"""Explicit candidate-count contract; isolated data and in-memory providers only."""
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

_import_directory = tempfile.TemporaryDirectory(prefix="travel-candidate-mode-import-")
atexit.register(_import_directory.cleanup)
with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": _import_directory.name}):
    module = importlib.import_module("backend.app")
_real_scope_request = agent.scope_request
_real_candidates_request = agent.candidates_request


class CandidateModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="travel-candidate-mode-test-")
        self.addCleanup(self.directory.cleanup)
        previous = module.store
        module.store = module.Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", previous)
        self.settings = Settings(agent_mode="llm", llm_api_key="fixture-only-candidate-mode", requests_per_minute=120)
        for owner in (module, agent, access):
            patcher = patch.object(owner, "get_settings", side_effect=lambda: self.settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        with access._bucket_lock:
            access._buckets.clear()
        self.scope_value = {"message": "Only the requested second-day interface", "permissions": [{"regionId": "day-2", "editSurface": True}]}
        self.plans = [self.plan(index) for index in range(3)]
        self.calls = []
        self.callback = None
        self.scope_patcher = patch.object(agent, "scope_request", side_effect=lambda *_args, **_kwargs: agent.EditScope.model_validate(deepcopy(self.scope_value)))
        self.scope_mock = self.scope_patcher.start()
        self.addCleanup(self.scope_patcher.stop)
        patcher = patch.object(agent, "candidates_request", side_effect=self.reply)
        self.candidates_mock = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(agent, "_request_model", side_effect=AssertionError("candidate mode tests cannot call a model"))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(module, "enrich_day", side_effect=AssertionError("widget candidate tests cannot call a map"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(module.app)
        self.addCleanup(self.client.close)
        self.session = self.client.post("/api/sessions").json()
        self.url = f"/api/sessions/{self.session['id']}"

    @staticmethod
    def plan(index):
        layouts = ["display:grid;grid-template-columns:1fr 1fr", "display:flex;flex-direction:column", "display:flex;flex-direction:row"]
        return {"title": f"Layout {index + 1}", "message": "Add only the requested packing panel", "scope": "selected", "operations": [
            {"kind": "set_surface", "regionId": "day-2", "surface": {
                "title": "Packing panel", "html": "<section><label><input type='checkbox'>Umbrella</label><label><input type='checkbox'>Charger</label></section>",
                "css": "section{" + layouts[index] + ";gap:12px}", "javascript": "", "placement": "afterStops"}}
        ]}

    def reply(self, *args, **kwargs):
        self.calls.append({"instruction": args[1], "scope": args[4].model_dump(), **deepcopy(kwargs)})
        plans = self.callback(len(self.calls)) if self.callback else self.plans
        return agent.CandidateBatch.model_validate({"message": "Prepared", "candidates": deepcopy(plans)})

    def generate(self, mode=None, *, include_mode=True):
        body = {"baseVersion": self.session["version"], "targetRegionId": "day-2", "instruction": "只为第二天增加准备清单，保留原行程和其他内容"}
        if include_mode:
            body["generateCandidates"] = mode
        return self.client.post(self.url + "/generate", json=body)

    def current(self):
        return self.client.get(self.url).json()

    def test_unchecked_returns_exactly_one_and_stores_mode_without_mutation(self):
        before = deepcopy(self.current())
        response = self.generate(False)
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()
        self.assertEqual(len(batch["candidates"]), 1)
        self.assertEqual(batch["candidateCount"], 1)
        self.assertEqual(batch["minimumCandidates"], 1)
        self.assertIs(batch["generateCandidates"], False)
        self.assertEqual(len(module.store.proposals), 1)
        self.assertIs(module.store.proposals[batch["proposalId"]]["generateCandidates"], False)
        self.assertEqual(self.calls[0]["generate_candidates"], False)
        self.assertEqual(self.scope_mock.call_args.kwargs, {"generate_candidates": False})
        self.assertEqual(self.current(), before)

    def test_checked_returns_three_distinct_scoped_results_and_modes(self):
        before = deepcopy(self.current())
        response = self.generate(True)
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()
        self.assertIs(batch["generateCandidates"], True)
        self.assertEqual(batch["candidateCount"], 3)
        self.assertEqual(batch["minimumCandidates"], 3)
        self.assertEqual(len(batch["candidates"]), 3)
        self.assertEqual(len({candidate["updates"][0]["nextUI"]["surface"]["css"] for candidate in batch["candidates"]}), 3)
        for candidate in batch["candidates"]:
            self.assertEqual(candidate["affectedRegionIds"], ["day-2"])
            self.assertIs(module.store.proposals[candidate["proposalId"]]["generateCandidates"], True)
            self.assertNotIn("nextData", candidate["updates"][0])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.current(), before)

    def test_checked_supplements_partial_batch_with_same_scope_and_original_instruction(self):
        self.callback = lambda call: [self.plan(0)] if call == 1 else [self.plan(0), self.plan(1), self.plan(2)]
        before = deepcopy(self.current())
        response = self.generate(True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidateCount"], 3)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.scope_mock.call_count, 1)
        self.assertEqual(self.calls[0]["scope"], self.calls[1]["scope"])
        self.assertEqual(self.calls[0]["instruction"], self.calls[1]["instruction"])
        self.assertTrue(all(call["generate_candidates"] is True for call in self.calls))
        self.assertTrue(any("目前只有1个" in item for item in self.calls[1]["feedback"]))
        self.assertTrue(any("已接受的候选" in item for item in self.calls[1]["feedback"]))
        self.assertEqual(self.current(), before)

    def test_checked_rejects_out_of_scope_candidate_then_supplements_without_query(self):
        bad = self.plan(0)
        bad["operations"].append({"kind": "set_note", "regionId": "day-1", "note": "Unrequested"})
        self.callback = lambda call: [bad, self.plan(0)] if call == 1 else [self.plan(1), self.plan(2)]
        before = deepcopy(self.current())
        response = self.generate(True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidateCount"], 3)
        self.assertEqual(self.current(), before)
        for candidate in response.json()["candidates"]:
            self.assertEqual(candidate["affectedRegionIds"], ["day-2"])
        self.assertTrue(any("有1个候选" in note for note in response.json()["candidateNotes"]))

    def test_titles_cannot_fake_three_distinct_generated_interfaces(self):
        self.plans = [self.plan(0) for _index in range(3)]
        for index, plan in enumerate(self.plans):
            plan["title"] = f"Different candidate title {index}"
            plan["operations"][0]["surface"]["title"] = f"Different wrapper title {index}"
        before = deepcopy(self.current())
        response = self.generate(True)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("仅得到1个", response.json()["detail"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.current(), before)
        self.assertFalse(module.store.proposals)

    def test_checked_does_not_return_only_two_after_bounded_retries(self):
        self.plans = [self.plan(0), self.plan(1)]
        before = deepcopy(self.current())
        response = self.generate(True)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("至少3个", response.json()["detail"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.current(), before)
        self.assertFalse(module.store.proposals)

    def test_explicit_boolean_does_not_accept_string_or_number_coercion(self):
        before = deepcopy(self.current())
        for value in ("true", "false", 1, 0, [], {}):
            with self.subTest(value=value):
                response = self.generate(value)
                self.assertEqual(response.status_code, 422, response.text)
        self.scope_mock.assert_not_called()
        self.candidates_mock.assert_not_called()
        self.assertEqual(self.current(), before)

    def test_omitted_mode_keeps_legacy_batch_behavior(self):
        response = self.generate(include_mode=False)
        self.assertEqual(response.status_code, 200, response.text)
        batch = response.json()
        self.assertIsNone(batch["generateCandidates"])
        self.assertEqual(batch["candidateCount"], 3)
        self.assertEqual(batch["minimumCandidates"], 1)
        self.assertNotIn("generate_candidates", self.calls[0])
        self.assertEqual(self.scope_mock.call_args.kwargs, {})

    def test_checked_repair_preserves_mode_and_refills_three(self):
        response = self.generate(True)
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()["candidates"][0]
        previous_scope_calls = self.scope_mock.call_count
        before = deepcopy(self.current())
        self.calls.clear()
        self.callback = lambda call: [self.plan(0)] if call == 1 else [self.plan(1), self.plan(2)]
        repaired = self.client.post(self.url + f"/proposals/{proposal['proposalId']}/repair", json={"baseVersion": self.session["version"], "error": "ReferenceError: counter is not defined"})
        self.assertEqual(repaired.status_code, 200, repaired.text)
        result = repaired.json()
        self.assertIs(result["generateCandidates"], True)
        self.assertEqual(result["candidateCount"], 3)
        self.assertEqual(result["minimumCandidates"], 3)
        self.assertTrue(all(call["generate_candidates"] is True for call in self.calls))
        self.assertEqual(self.scope_mock.call_count, previous_scope_calls)
        self.assertTrue(any("counter" in item for item in self.calls[1]["feedback"]))
        self.assertNotIn(proposal["proposalId"], module.store.proposals)
        self.assertEqual(self.current(), before)
        for candidate in result["candidates"]:
            entry = module.store.proposals[candidate["proposalId"]]
            self.assertIs(entry["generateCandidates"], True)
            self.assertEqual(entry["repairCount"], 1)

    def test_unchecked_repair_cannot_reintroduce_multiple_choices(self):
        response = self.generate(False)
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()["candidates"][0]
        repaired = self.client.post(self.url + f"/proposals/{proposal['proposalId']}/repair", json={"baseVersion": self.session["version"], "error": "ReferenceError: fix the handler"})
        self.assertEqual(repaired.status_code, 200, repaired.text)
        self.assertEqual(repaired.json()["candidateCount"], 1)
        self.assertIs(repaired.json()["generateCandidates"], False)
        self.assertEqual(self.calls[-1]["generate_candidates"], False)

    def test_checked_apply_and_undo_preserve_trip_and_unrelated_days(self):
        before = deepcopy(self.current())
        response = self.generate(True)
        self.assertEqual(response.status_code, 200, response.text)
        chosen = response.json()["candidates"][2]
        applied = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": chosen["proposalId"]})
        self.assertEqual(applied.status_code, 200, applied.text)
        saved = applied.json()
        self.assertEqual(saved["tripState"], before["tripState"])
        for region in ("day-1", "day-3"):
            self.assertEqual(saved["uiSpec"]["regions"][region], before["uiSpec"]["regions"][region])
        self.assertEqual(saved["uiSpec"]["regions"]["day-2"], chosen["updates"][0]["nextUI"])
        undone = self.client.post(self.url + "/undo", json={"baseVersion": saved["version"]})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()["tripState"], before["tripState"])
        self.assertEqual(undone.json()["uiSpec"], before["uiSpec"])

    def test_model_context_includes_checkbox_count_but_preserves_original_instruction(self):
        scope = agent.EditScope.model_validate(self.scope_value)
        for mode in (False, True):
            with self.subTest(mode=mode):
                with patch.object(agent, "_request_model", return_value=scope) as provider:
                    _real_scope_request(self.session, "Keep this original text", "day-2", module.PLACES, generate_candidates=mode)
                context = provider.call_args.args[0]
                self.assertEqual(context["instruction"], "Keep this original text")
                self.assertEqual(context["generationPreferences"], {"generateCandidates": mode, "minimumCandidates": 3 if mode else 1, "maximumCandidates": 3 if mode else 1})
                with patch.object(agent, "_request_model", return_value=agent.CandidateBatch(message="Ready", candidates=[agent.CandidatePlan.model_validate(self.plan(0))])) as provider:
                    _real_candidates_request(self.session, "Keep this original text", "day-2", module.PLACES, scope, generate_candidates=mode)
                context = provider.call_args.args[0]
                self.assertEqual(context["instruction"], "Keep this original text")
                self.assertEqual(context["generationPreferences"]["generateCandidates"], mode)
                self.assertEqual(context["frozenScope"], scope.model_dump(exclude_none=True))


if __name__ == "__main__":
    unittest.main()