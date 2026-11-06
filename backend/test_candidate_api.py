"""Phase 2 candidate contracts and write boundaries on isolated SQLite only.

No external model/maps are used. One HTTP fixture validates the real httpx adapter
against a local chat/completions server; its key is an inert synthetic test value.
"""
from __future__ import annotations

import atexit
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
import httpx

from backend import access, agent, candidate_agent, candidate_api, maps
from backend.config import Settings, get_settings
from backend.patch_guard import leaf_diff, resolve_scope, validate_patch

_import_directory = tempfile.TemporaryDirectory(prefix="travel-candidate-import-")
atexit.register(_import_directory.cleanup)
with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": _import_directory.name}):
    module = importlib.import_module("backend.app")


class CandidateAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="travel-candidate-api-")
        self.addCleanup(self.directory.cleanup)
        previous = module.store
        module.store = module.Store(Path(self.directory.name))
        self.addCleanup(setattr, module, "store", previous)
        self.settings = Settings(agent_mode="demo", candidate_agent_mode="mock", llm_api_key="", amap_key="", requests_per_minute=120)
        for owner in (module, agent, access, maps, candidate_api, candidate_agent):
            p = patch.object(owner, "get_settings", side_effect=lambda: self.settings)
            p.start(); self.addCleanup(p.stop)
        with access._bucket_lock:
            access._buckets.clear()
        p = patch.object(maps, "_request_once", side_effect=AssertionError("No external maps in Phase 2 tests"))
        p.start(); self.addCleanup(p.stop)
        self.client = TestClient(module.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/sessions", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.url = "/api/sessions/" + self.session["id"]
        self.pm = self.binding("PeriodGroup", dayKey="day2", period="pm")

    def binding(self, kind, **attributes):
        return next(item for item in self.session["componentRegistry"].values()
                    if item["kind"] == kind and all(item.get(key) == value for key, value in attributes.items()))

    def stored(self):
        return deepcopy(module.store.get(self.session["id"]))

    def candidates(self, **changes):
        body = {"baseVersion": self.session["version"], "selectedComponentIds": [self.pm["componentId"]],
                "instruction": "安排轻松一些", "agentMode": "mock"}
        body.update(changes)
        return self.client.post(self.url + "/edits/candidates", json=body)

    def batch(self, **changes):
        response = self.candidates(**changes)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def commit(self, candidate, **changes):
        body = {"baseVersion": self.session["version"], "candidateId": candidate["candidateId"]}
        body.update(changes)
        return self.client.post(self.url + "/edits/commit", json=body)

    def lock(self, kind="DayCard", **attributes):
        target = self.binding(kind, **attributes)
        response = self.client.patch(self.url + "/editor/locks", json={"baseVersion": self.session["version"],
            "componentId": target["componentId"], "locked": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()

    def assert_unchanged(self, before):
        self.assertEqual(self.stored(), before)
        current = self.client.get(self.url).json()
        self.assertEqual(current["version"], before["version"])
        self.assertEqual(current["tripState"], before["tripState"])
        self.assertEqual(current["persistedConstraints"], before["persistedConstraints"])

    def generated_proposals(self):
        snapshot = module.normalize_session(self.stored())
        scope = resolve_scope(snapshot, [self.pm["componentId"]])
        return candidate_agent.mock_generate(snapshot, "安排轻松一些", scope)

    def test_three_mock_candidates_share_snapshot_but_differ_in_real_patches(self):
        self.lock(dayKey="day1"); self.lock(dayKey="day3")
        before = self.stored()
        batch = self.batch()
        self.assertEqual(batch["generationMode"], "mock")
        self.assertEqual(batch["candidateCount"], 3)
        self.assertEqual([c["strategy"] for c in batch["candidates"]], list(candidate_agent.STRATEGIES))
        self.assertEqual({c["baseVersion"] for c in batch["candidates"]}, {before["version"]})
        docs = [c["document"] for c in batch["candidates"]]
        self.assertEqual(len({json.dumps(d, sort_keys=True) for d in docs}), 3)
        patches = [c["patch"] for c in batch["candidates"]]
        self.assertTrue(all(op["path"].endswith("/description") for op in patches[0]))
        self.assertTrue(any(op["path"].endswith("/startTime") for op in patches[1]))
        self.assertNotEqual(patches[1], patches[2])
        times = [[a["startTime"] for a in d["entities"]["activities"].values() if a["period"] == "pm"] for d in docs]
        self.assertNotEqual(times[1], times[2])
        for c in batch["candidates"]:
            self.assertTrue(all(c["validationReport"].values()))
            self.assertEqual(leaf_diff(self.session["tripDocument"], c["document"]), c["changedPaths"])
            self.assertEqual({d["path"] for d in c["differences"]}, set(c["changedPaths"]))
            self.assertLessEqual(set(c["changedPaths"]), set(batch["effectiveScope"]["allowedPaths"]))
        self.assert_unchanged(before)

    def test_only_candidate_b_commits_authorized_fields_and_version_plus_one(self):
        self.lock(dayKey="day1"); self.lock(dayKey="day3")
        before = self.stored(); batch = self.batch(); chosen = batch["candidates"][1]
        checked = validate_patch(before, [self.pm["componentId"]], chosen["patch"])
        response = self.commit(chosen)
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()
        self.assertEqual(current["version"], before["version"] + 1)
        self.assertEqual(current["tripState"], checked["payload"]["tripState"])
        self.assertEqual(current["tripState"]["days"]["day1"], before["tripState"]["days"]["day1"])
        self.assertEqual(current["tripState"]["days"]["day3"], before["tripState"]["days"]["day3"])
        self.assertEqual(current["persistedConstraints"], before["persistedConstraints"])
        audit = current["editorAudit"]
        self.assertEqual(audit["candidateId"], chosen["candidateId"])
        self.assertEqual(audit["strategy"], "efficiency_first")
        self.assertEqual(audit["userRequest"], "安排轻松一些")
        self.assertEqual(audit["generationMode"], "mock")
        self.assertEqual(audit["patch"], chosen["patch"])
        self.assertEqual(audit["constraintsSnapshot"], before["persistedConstraints"])
        self.assertEqual(len(current["history"]), len(before["history"]) + 1)
        # A and C are never applied and become invalid after any accepted commit.
        self.session = current
        after = self.stored()
        for c in (batch["candidates"][0], batch["candidates"][2], chosen):
            self.assertEqual(self.commit(c).status_code, 410)
            self.assert_unchanged(after)

    def test_generation_is_deterministic_but_server_ids_are_new(self):
        first, second = self.batch(), self.batch()
        self.assertEqual([c["patch"] for c in first["candidates"]], [c["patch"] for c in second["candidates"]])
        self.assertNotEqual(first["candidates"][0]["candidateId"], second["candidates"][0]["candidateId"])

    def test_no_selection_rejected_before_adapter_and_no_write(self):
        before = self.stored()
        with patch.object(candidate_agent, "generate") as generate:
            response = self.candidates(selectedComponentIds=[])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["code"], "NO_SELECTION")
        generate.assert_not_called(); self.assert_unchanged(before)

    def test_parent_day_selection_is_limited_to_the_selected_days_children(self):
        day2 = self.binding("DayCard", dayKey="day2")
        batch = self.batch(selectedComponentIds=[day2["componentId"]])
        permitted = set(resolve_scope(self.stored(), [day2["componentId"]])["allowedPaths"])
        self.assertEqual(set(batch["effectiveScope"]["allowedPaths"]), permitted)
        self.assertEqual(len(batch["effectiveScope"]["eligibleActivityIds"]), 3)

    def test_other_day_selection_is_authorised_without_mutating_the_base(self):
        before = self.stored()
        for key in ("day1", "day3"):
            day = self.binding("DayCard", dayKey=key)
            batch = self.batch(selectedComponentIds=[day["componentId"]])
            self.assertEqual(batch["candidateCount"], 3)
            allowed = resolve_scope(self.stored(), [day["componentId"]])["allowedPaths"]
            self.assertEqual(batch["effectiveScope"]["allowedPaths"], allowed)
            self.assert_unchanged(before)

    def test_locked_selection_rejected_before_adapter(self):
        self.lock(dayKey="day2"); before = self.stored()
        with patch.object(candidate_agent, "generate") as generate:
            response = self.candidates()
        self.assertEqual(response.status_code, 423); generate.assert_not_called(); self.assert_unchanged(before)

    def test_client_cannot_supply_scope_or_locked_ids(self):
        before = self.stored()
        for key, value in (("allowedPaths", ["/title"]), ("effectiveScope", {}), ("lockedIds", []), ("patch", [])):
            with self.subTest(key=key):
                self.assertEqual(self.candidates(**{key: value}).status_code, 422)
                self.assert_unchanged(before)

    def test_commit_rejects_client_patch_scope_selection_and_extra_values(self):
        chosen = self.batch()["candidates"][1]; before = self.stored()
        for key, value in (("patch", chosen["patch"]), ("allowedPaths", []), ("lockedIds", []), ("selectedComponentIds", [])):
            with self.subTest(key=key):
                self.assertEqual(self.commit(chosen, **{key: value}).status_code, 422)
                self.assert_unchanged(before)

    def test_malicious_model_locked_day_patch_is_rejected_and_not_cached(self):
        self.lock(dayKey="day1"); self.lock(dayKey="day3"); before = self.stored()
        proposals = self.generated_proposals()
        target = self.binding("ActivityCard", dayKey="day1")
        proposals[0]["patch"].append({"op": "replace", "path": f"/entities/activities/{target['entityId']}/description", "value": "越界"})
        with patch.object(candidate_agent, "generate", return_value=proposals):
            batch = self.batch(agentMode="llm")
        self.assertEqual(batch["candidateCount"], 2)
        self.assertNotIn("minimal_change", {c["strategy"] for c in batch["candidates"]})
        self.assertTrue(any(item["code"] == "LOCKED_COMPONENT" for item in batch["rejectedCandidates"]))
        self.assert_unchanged(before)

    def test_all_invalid_candidates_produce_clear_failure_without_partial_write(self):
        before = self.stored(); proposals = self.generated_proposals()
        for proposal in proposals:
            proposal["patch"] = [{"op": "replace", "path": "/entities", "value": "malicious"}]
        with patch.object(candidate_agent, "generate", return_value=proposals):
            response = self.candidates(agentMode="llm")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "NO_VALID_CANDIDATES")
        self.assertFalse(module.store.proposals)
        self.assert_unchanged(before)

    def test_model_self_evaluation_and_executable_fields_are_not_accepted(self):
        before = self.stored(); proposals = self.generated_proposals()
        for key, value in (("evaluation", {"valid": True, "score": 1}), ("score", 1), ("candidateId", "fake"), ("allowedPaths", ["/title"]), ("html", "<script>1</script>")):
            with self.subTest(key=key):
                forged = deepcopy(proposals)
                for item in forged: item[key] = value
                with patch.object(candidate_agent, "generate", return_value=forged):
                    response = self.candidates(agentMode="llm")
                self.assertEqual(response.status_code, 422)
                self.assert_unchanged(before)

    def test_identical_and_description_only_rewordings_are_filtered(self):
        proposals = self.generated_proposals(); original = deepcopy(proposals[0]["patch"])
        for proposal in proposals: proposal["patch"] = deepcopy(original)
        proposals[1]["patch"][0]["value"] += "不同文案"
        proposals[2]["patch"][0]["value"] += "第三套说法"
        with patch.object(candidate_agent, "generate", return_value=proposals):
            batch = self.batch(agentMode="llm")
        self.assertEqual(batch["candidateCount"], 1)
        self.assertEqual(len(batch["rejectedCandidates"]), 2)
        self.assertTrue(all(item["code"] == "SIMILAR_CANDIDATE" for item in batch["rejectedCandidates"]))
        self.assertTrue(any("仅有1个" in item for item in batch["diagnostics"]))

    def test_restricted_description_field_reports_shortage_instead_of_faking_three(self):
        before = self.stored(); batch = self.batch(fields=["description"])
        self.assertEqual(batch["candidateCount"], 1)
        self.assertTrue(all(op["path"].endswith("/description") for c in batch["candidates"] for op in c["patch"]))
        self.assertTrue(batch["rejectedCandidates"])
        self.assert_unchanged(before)

    def test_duplicate_strategy_is_filtered(self):
        proposals = self.generated_proposals(); proposals[1]["strategy"] = proposals[0]["strategy"]
        with patch.object(candidate_agent, "generate", return_value=proposals): batch = self.batch()
        self.assertEqual(batch["candidateCount"], 2)
        self.assertTrue(any(item["code"] == "DUPLICATE_STRATEGY" for item in batch["rejectedCandidates"]))

    def test_mock_unknown_or_outside_scope_intent_is_explicit_failure(self):
        before = self.stored()
        for instruction in ("新增一个搜索框", "换景点让行程轻松一些", "第一天安排轻松一些", "每天加地图"):
            with self.subTest(instruction=instruction):
                response = self.candidates(instruction=instruction)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "MOCK_UNSUPPORTED_INTENT")
                self.assert_unchanged(before)

    def test_mock_preservation_mentions_are_valid_and_do_not_expand_scope(self):
        self.lock(dayKey="day1"); self.lock(dayKey="day3"); before = self.stored()
        batch = self.batch(instruction="把第二天下午安排轻松一些，保留第一天和第三天")
        self.assertEqual(batch["candidateCount"], 3)
        self.assert_unchanged(before)

    def test_mock_explicit_preserved_time_is_respected_and_shortage_explained(self):
        batch = self.batch(instruction="安排轻松一些，保留原时间")
        self.assertEqual(batch["candidateCount"], 1)
        self.assertTrue(all(op["path"].endswith("/description") for c in batch["candidates"] for op in c["patch"]))

    def test_mock_place_replacement_can_preserve_time(self):
        before_times = {
            activity_id: activity["startTime"]
            for activity_id, activity in self.session["tripDocument"]["entities"]["activities"].items()
        }
        batch = self.batch(instruction="换个景点，保留时间和其他内容", fields=["placeId"])
        self.assertEqual(batch["candidateCount"], 3)
        self.assertTrue(all(op["path"].endswith("/placeId") for c in batch["candidates"] for op in c["patch"]))
        for candidate in batch["candidates"]:
            after_times = {
                activity_id: activity["startTime"]
                for activity_id, activity in candidate["document"]["entities"]["activities"].items()
            }
            self.assertEqual(after_times, before_times)

    def test_missing_llm_key_explains_configuration_and_never_calls_network(self):
        before = self.stored()
        with patch.object(candidate_agent.httpx, "Client") as network:
            response = self.candidates(agentMode="llm")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("LLM_API_KEY", response.json()["detail"]["message"])
        network.assert_not_called(); self.assert_unchanged(before)

    def test_status_exposes_mode_but_never_secret(self):
        self.settings = replace(self.settings, llm_api_key="inert-secret-test-only")
        response = self.client.get("/api/edits/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["llmConfigured"])
        self.assertEqual(response.json()["defaultMode"], "mock")
        self.assertNotIn(self.settings.llm_api_key, response.text)
        state = self.client.get("/api/agent/status").json()
        self.assertEqual(state["phase"], "Phase 4")
        self.assertFalse(state["runtimeCodeExecution"])

    def test_expired_candidate_refuses_without_partial_update(self):
        chosen = self.batch()["candidates"][1]; before = self.stored()
        module.store.proposals[chosen["candidateId"]]["expiresMonotonic"] = 0
        response = self.commit(chosen)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["detail"]["code"], "CANDIDATE_EXPIRED")
        self.assert_unchanged(before)

    def test_old_base_version_refuses_without_partial_update(self):
        chosen = self.batch()["candidates"][1]
        self.lock(dayKey="day1"); before = self.stored()
        response = self.commit(chosen, baseVersion=chosen["baseVersion"])
        self.assertEqual(response.status_code, 409)
        self.assert_unchanged(before)

    def test_stale_candidate_cannot_use_new_client_version(self):
        chosen = self.batch()["candidates"][1]
        self.lock(dayKey="day1"); before = self.stored()
        response = self.commit(chosen)
        self.assertEqual(response.status_code, 409)
        self.assert_unchanged(before)

    def test_unknown_candidate_refuses_without_partial_update(self):
        before = self.stored(); response = self.commit({"candidateId": "p2-nonexistent"})
        self.assertEqual(response.status_code, 410); self.assert_unchanged(before)

    def test_commit_revalidates_server_cached_patch_instead_of_validation_report(self):
        chosen = self.batch()["candidates"][1]; before = self.stored()
        cached = module.store.proposals[chosen["candidateId"]]
        cached["patch"].append({"op": "replace", "path": "/title", "value": "forbidden"})
        cached["validationReport"] = {"valid": True}
        response = self.commit(chosen)
        self.assertEqual(response.status_code, 403); self.assert_unchanged(before)

    def test_commit_rechecks_current_locks_even_if_unversioned_fault_occurs(self):
        chosen = self.batch()["candidates"][1]
        # Fault injection only in isolated test SQLite: simulate an unversioned
        # lock write to prove commit does not trust a cached valid flag.
        with module.store._connect() as connection:
            state = self.stored(); state["persistedConstraints"]["lockedComponentIds"].append(self.pm["componentId"])
            connection.execute("UPDATE sessions SET document=? WHERE id=?", (json.dumps(state), self.session["id"]))
        before = self.stored(); response = self.commit(chosen)
        self.assertEqual(response.status_code, 423); self.assert_unchanged(before)

    def test_version_changes_during_model_generation_returns_409_and_caches_nothing(self):
        proposals = self.generated_proposals()
        def generate(*args):
            with module.store.lock:
                current = module.store.get(self.session["id"])
                current["version"] += 1; module.store.save(current)
            return proposals
        with patch.object(candidate_agent, "generate", side_effect=generate): response = self.candidates()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(module.store.proposals)
        self.assertEqual(self.stored()["tripState"], self.session["tripState"])

    def test_network_generation_does_not_hold_store_lock(self):
        proposals = self.generated_proposals(); acquired = []
        def generate(*args):
            def other_thread():
                ok = module.store.lock.acquire(timeout=1)
                acquired.append(ok)
                if ok: module.store.lock.release()
            worker = threading.Thread(target=other_thread); worker.start(); worker.join()
            return proposals
        with patch.object(candidate_agent, "generate", side_effect=generate): self.batch()
        self.assertEqual(acquired, [True])

    def test_cookie_ownership_covers_both_new_routes(self):
        chosen = self.batch()["candidates"][0]; before = self.stored()
        with TestClient(module.app, raise_server_exceptions=False) as other:
            self.assertEqual(other.post(self.url + "/edits/candidates", json={"baseVersion": self.session["version"],
                "selectedComponentIds": [self.pm["componentId"]], "instruction": "安排轻松一些"}).status_code, 404)
            self.assertEqual(other.post(self.url + "/edits/commit", json={"baseVersion": self.session["version"],
                "candidateId": chosen["candidateId"]}).status_code, 404)
        self.assert_unchanged(before)

    def test_new_session_cannot_commit_foreign_candidate_even_same_visitor(self):
        chosen = self.batch()["candidates"][0]; before = self.stored()
        other = self.client.post("/api/sessions", json={}).json()
        response = self.client.post("/api/sessions/" + other["id"] + "/edits/commit", json={"baseVersion": other["version"], "candidateId": chosen["candidateId"]})
        self.assertEqual(response.status_code, 410); self.assert_unchanged(before)

    def test_two_concurrent_commits_result_in_exactly_one_success(self):
        batch = self.batch(); responses = []
        barrier = threading.Barrier(2)
        cookies = dict(self.client.cookies)
        def commit(candidate):
            with TestClient(module.app, raise_server_exceptions=False, cookies=cookies) as client:
                barrier.wait()
                responses.append(client.post(self.url + "/edits/commit", json={"baseVersion": self.session["version"], "candidateId": candidate["candidateId"]}).status_code)
        workers = [threading.Thread(target=commit, args=(c,)) for c in batch["candidates"][:2]]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=10)
        self.assertEqual(sorted(responses), [200, 409])
        self.assertEqual(self.stored()["version"], self.session["version"] + 1)

    @contextmanager
    def upstream(self, response=None, error=None):
        # Replace only the adapter constructor, not httpx.Client.post: TestClient
        # inherits that method and must continue to exercise the real API route.
        with patch.object(candidate_agent.httpx, "Client") as factory:
            client = factory.return_value.__enter__.return_value
            client.post.return_value = response
            client.post.side_effect = error
            yield client

    def test_llm_timeout_is_clear_and_does_not_mutate(self):
        before = self.stored(); self.settings = replace(self.settings, llm_api_key="inert-test-key")
        with self.upstream(error=httpx.ReadTimeout("fake timeout")):
            response = self.candidates(agentMode="llm")
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["detail"]["code"], "MODEL_TIMEOUT")
        self.assert_unchanged(before)

    def model_response(self, content, status=200, finish="stop"):
        return httpx.Response(status, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]})

    def test_invalid_model_json_duplicate_keys_and_extra_envelope_refused(self):
        before = self.stored(); self.settings = replace(self.settings, llm_api_key="inert-test-key")
        for content in ("not JSON", "{", '{"candidates": [], "evaluation": {"valid": true}}', '{"candidates": [], "candidates": []}', '{"candidates": [NaN]}'):
            with self.subTest(content=content), self.upstream(response=self.model_response(content)):
                response = self.candidates(agentMode="llm")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(response.json()["detail"]["code"], "MODEL_INVALID_JSON")
                self.assert_unchanged(before)

    def test_model_auth_rate_http_and_incomplete_errors_do_not_expose_upstream(self):
        before = self.stored(); self.settings = replace(self.settings, llm_api_key="inert-test-key")
        for status, expected in ((401, 502), (403, 502), (429, 429), (500, 502)):
            with self.subTest(status=status), self.upstream(response=httpx.Response(status, text="upstream synthetic secret")):
                response = self.candidates(agentMode="llm")
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("synthetic secret", response.text); self.assert_unchanged(before)
        with self.upstream(response=self.model_response('{}', finish="length")):
            self.assertEqual(self.candidates(agentMode="llm").status_code, 502)
        self.assert_unchanged(before)

    def test_real_httpx_llm_adapter_against_local_chat_completions_fixture(self):
        proposals = self.generated_proposals(); captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                content = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"candidates": proposals}, ensure_ascii=False)}}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            self.settings = replace(self.settings, llm_api_key="local-fixture-only", llm_base_url=f"http://127.0.0.1:{server.server_port}/v1", llm_model="fixture-json-model")
            before = self.stored(); batch = self.batch(agentMode="llm")
            self.assertEqual(batch["generationMode"], "llm")
            self.assertEqual(batch["candidateCount"], 3)
            self.assertEqual(captured[0]["path"], "/v1/chat/completions")
            self.assertEqual(captured[0]["headers"]["Authorization"], "Bearer local-fixture-only")
            self.assertEqual(captured[0]["body"]["model"], "fixture-json-model")
            prompt = json.loads(captured[0]["body"]["messages"][1]["content"])
            Draft202012Validator.check_schema(prompt["outputSchema"])
            Draft202012Validator(prompt["outputSchema"]).validate({"candidates": proposals})
            self.assertEqual(prompt["baseVersion"], self.session["version"])
            self.assertEqual(prompt["effectiveScope"]["allowedPaths"], batch["effectiveScope"]["allowedPaths"])
            self.assert_unchanged(before)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=3)

    def test_candidate_configuration_defaults_and_invalid_mode(self):
        with patch.dict(os.environ, {"TRAVEL_ENV_FILE": str(Path(self.directory.name) / "absent"), "CANDIDATE_AGENT_MODE": "mock", "CANDIDATE_TTL_SECONDS": "90"}):
            settings = get_settings()
            self.assertEqual(settings.candidate_agent_mode, "mock")
            self.assertEqual(settings.candidate_ttl_seconds, 90)
        with patch.dict(os.environ, {"TRAVEL_ENV_FILE": str(Path(self.directory.name) / "absent"), "CANDIDATE_AGENT_MODE": "javascript"}):
            with self.assertRaises(ValueError): get_settings()


    def test_catalog_replacements_are_three_real_distinct_candidates_and_commit_only_one(self):
        before = self.stored()
        batch = self.batch(instruction="换个景点", fields=["placeId"])
        self.assertEqual(batch["candidateCount"], 3)
        self.assertEqual(len({c["patch"][0]["value"] for c in batch["candidates"]}), 3)
        for candidate in batch["candidates"]:
            self.assertIn("替换1个", candidate["rationale"])
            self.assertNotIn("保留原时间与景点", candidate["rationale"])
            self.assertTrue(candidate["derivedChangedPaths"])
            self.assertTrue(all(op["path"].endswith("/placeId") for op in candidate["patch"]))
        self.assert_unchanged(before)
        chosen = batch["candidates"][1]
        response = self.commit(chosen)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["version"], self.session["version"] + 1)
        expected = deepcopy(chosen["document"]); expected["version"] += 1
        self.assertEqual(saved["tripDocument"], expected)
        version = self.client.get(self.url + "/versions/" + str(saved["version"])).json()
        self.assertEqual(version["derivedChangedPaths"], chosen["derivedChangedPaths"])
        self.assertEqual(saved["tripState"]["days"]["day1"], self.session["tripState"]["days"]["day1"])

    def test_mock_specific_time_crosses_period_without_fabricating_alternative_times(self):
        binding = self.binding("ActivityCard", dayKey="day3", period="am")
        # The first stop cannot move past the next stop; the last stop can move to evening.
        bindings = [item for item in self.session["componentRegistry"].values() if item["kind"] == "ActivityCard" and item["dayKey"] == "day3"]
        item = max(bindings, key=lambda x: self.session["tripDocument"]["entities"]["activities"][x["entityId"]]["startTime"])
        batch = self.batch(selectedComponentIds=[item["componentId"]], instruction="把开始时间改为19:00", fields=["startTime"])
        self.assertEqual(batch["candidateCount"], 1)
        candidate = batch["candidates"][0]
        self.assertIn("调整1个开始时间", candidate["rationale"])
        self.assertEqual(candidate["document"]["entities"]["activities"][item["entityId"]]["startTime"], "19:00")
        self.assertEqual(candidate["document"]["entities"]["activities"][item["entityId"]]["period"], "evening")
        self.assertEqual(len(candidate["derivedChangedPaths"]), 2)

    def test_direct_time_move_updates_period_and_rejects_locked_destination(self):
        items = [item for item in self.session["componentRegistry"].values() if item["kind"] == "ActivityCard" and item["dayKey"] == "day3"]
        item = max(items, key=lambda x: self.session["tripDocument"]["entities"]["activities"][x["entityId"]]["startTime"])
        evening = self.binding("PeriodGroup", dayKey="day3", period="evening")
        self.lock(kind="PeriodGroup", dayKey="day3", period="evening")
        before = self.stored()
        body = {"baseVersion": self.session["version"], "kind": "time", "targetRegionId": "day-3", "stopId": item["stopId"], "value": "19:00"}
        denied = self.client.patch(self.url + "/direct", json=body)
        self.assertEqual(denied.status_code, 423, denied.text)
        self.assert_unchanged(before)
        unlock = self.client.patch(self.url + "/editor/locks", json={"baseVersion": self.session["version"], "componentId": evening["componentId"], "locked": False})
        self.session = unlock.json(); body["baseVersion"] = self.session["version"]
        saved = self.client.patch(self.url + "/direct", json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["tripDocument"]["entities"]["activities"][item["entityId"]]["period"], "evening")
        self.assertEqual(saved.json()["componentRegistry"][item["componentId"]]["parentId"], evening["componentId"])

    def test_mock_card_instruction_can_mention_its_actual_day(self):
        card = self.binding("ActivityCard", dayKey="day1", period="am")
        for instruction in ("把第一天这个景点安排轻松一些", "把第1天这个景点安排轻松一些"):
            batch = self.batch(selectedComponentIds=[card["componentId"]], instruction=instruction)
            self.assertEqual(batch["candidateCount"], 3)

if __name__ == "__main__":
    unittest.main()
