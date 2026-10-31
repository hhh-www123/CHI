"""Phase 1 runtime gate contracts. Legacy model transport fixtures remain inert.

The six former live-edit expectations were superseded by the explicit user ban
on runtime HTML/JS generation. These tests assert denial, zero upstream calls
and unchanged state; new Patch/ownership/version cases live in test_editor_api.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import agent
from backend import app as module
from backend.config import Settings


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_store = module.store
        module.store = module.Store(Path(self.temporary.name))
        self.settings = Settings(llm_api_key="test-secret-do-not-echo", llm_model="deepseek-flash")
        self.app_settings = patch.object(module, "get_settings", side_effect=lambda: self.settings)
        self.agent_settings = patch.object(agent, "get_settings", side_effect=lambda: self.settings)
        self.app_settings.start()
        self.agent_settings.start()
        self.client = TestClient(module.app)
        self.session = self.client.post("/api/sessions").json()
        self.url = f"/api/sessions/{self.session['id']}"
        self.calls = []
        self.reply = {"message": "为每天添加交通方式与地图", "scope": "all_days", "operations": [{"kind": "set_ui", "regionId": region, "showTransport": True, "showMap": True} for region in module.REGIONS], "clarification": None, "unsupportedCapabilities": []}
        self.upstream_status = 200
        self.before_reply = None
        original = httpx.Client
        self.transport = httpx.MockTransport(self.respond)
        self.http_patch = patch.object(agent.httpx, "Client", side_effect=lambda **kwargs: original(transport=self.transport, **kwargs))
        self.http_patch.start()
        self.map_patch = patch.object(module, "enrich_day", return_value={"status": "unconfigured", "message": "地图服务未配置", "provider": "amap", "points": [], "segments": []})
        self.map_mock = self.map_patch.start()

    def tearDown(self):
        self.client.close()
        self.map_patch.stop()
        self.http_patch.stop()
        self.agent_settings.stop()
        self.app_settings.stop()
        module.store = self.previous_store
        self.temporary.cleanup()

    def respond(self, request):
        self.calls.append(json.loads(request.content))
        if self.before_reply:
            callback, self.before_reply = self.before_reply, None
            callback()
        if self.upstream_status != 200:
            return httpx.Response(self.upstream_status, json={"error": "test-secret-do-not-echo"})
        context = json.loads(self.calls[-1]["messages"][1]["content"])
        reply = self.reply
        if isinstance(reply, dict) and context["phase"] == "scope":
            permissions = {}
            for operation in reply.get("operations", []):
                region = operation["regionId"]
                permission = permissions.setdefault(region, {"regionId": region, "uiFields": [], "requiredUI": {}, "placeStopIds": [], "timeStopIds": [], "editableBlockIds": [], "removableBlockIds": [], "newBlockTypes": []})
                kind = operation["kind"]
                if kind == "set_ui":
                    fields = {key: value for key, value in operation.items() if key not in {"kind", "regionId"}}
                    permission["uiFields"].extend(fields)
                    permission["requiredUI"].update(fields)
                elif kind == "replace_stop":
                    permission["placeStopIds"].append(operation["stopId"])
                elif kind == "set_time":
                    permission["timeStopIds"].append(operation["stopId"])
                elif kind == "set_note":
                    permission["editNote"] = True
                elif kind == "remove_block":
                    permission["removableBlockIds"].append(operation["blockId"])
                elif kind == "upsert_block":
                    existing = {item["id"] for item in context["current"]["uiSpec"]["regions"][region]["blocks"]}
                    if operation["block"]["id"] in existing:
                        permission["editableBlockIds"].append(operation["block"]["id"])
                    else:
                        permission["newBlockTypes"].append(operation["block"]["type"])
                else:
                    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"invalid":true}'}}]})
            reply = {"message": reply["message"], "permissions": list(permissions.values()), "clarification": reply.get("clarification"), "unsupportedCapabilities": reply.get("unsupportedCapabilities", [])}
        elif isinstance(reply, dict) and context["phase"] == "candidates":
            reply = {"message": reply["message"], "candidates": [reply]}
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    def preview(self, text="在每天游玩的经典间添加交通方式以及地图", target="day-2"):
        return self.client.post(self.url + "/generate", json={"baseVersion": self.session["version"], "targetRegionId": target, "instruction": text})

    def apply(self, proposal):
        response = self.client.post(self.url + "/apply", json={"baseVersion": self.session["version"], "proposalId": proposal["proposalId"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session

    def test_legacy_natural_request_is_disabled_without_model_or_state_changes(self):
        before = self.client.get(self.url).json()
        response = self.preview()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("旧自由代码生成入口已停用", response.json()["detail"])
        self.assertFalse(self.calls)
        self.assertEqual(self.map_mock.call_count, 0)
        self.assertEqual(self.client.get(self.url).json(), before)

    def test_one_locked_day_blocks_whole_multi_day_update(self):
        result = self.client.patch(self.url + "/locks", json={"baseVersion": self.session["version"], "target": "day-1", "locked": True})
        self.session = result.json()
        before = deepcopy(self.session)
        response = self.preview()
        self.assertEqual(response.status_code, 423, response.text)
        self.assertIn("第1天", response.json()["detail"])
        self.assertEqual(self.client.get(self.url).json(), before)
        self.assertFalse(self.calls, "明确知道锁定冲突时无需付费调用模型")

    def test_free_text_in_any_language_cannot_reactivate_legacy_model(self):
        before = self.client.get(self.url).json()
        for text in ["把最后一天的返程准备列出来", "Please show a route map for every day of this trip"]:
            with self.subTest(text=text):
                self.assertEqual(self.preview(text).status_code, 503)
        self.assertFalse(self.calls)
        self.assertEqual(self.client.get(self.url).json(), before)

    def test_missing_key_fails_without_demo_fallback_or_data_changes(self):
        self.settings = Settings(llm_api_key="")
        before = self.client.get(self.url).json()
        response = self.preview()
        self.assertEqual(response.status_code, 503)
        self.assertIn("旧自由代码生成入口已停用", response.json()["detail"])
        self.assertFalse(self.calls)
        self.assertEqual(self.client.get(self.url).json(), before)
        status = self.client.get("/api/agent/status").json()
        self.assertFalse(status["configured"])
        self.assertNotIn("llm_api_key", status)

    def test_disabled_generation_cannot_trigger_upstream_side_effects(self):
        def forbidden_callback():
            self.fail("停用的旧入口不应该发送模型请求")
        self.before_reply = forbidden_callback
        before = self.client.get(self.url).json()
        self.assertEqual(self.preview().status_code, 503)
        self.assertFalse(self.calls)
        self.assertEqual(self.client.get(self.url).json(), before)

    def test_disabled_model_contract_never_exposes_secrets_or_writes_state(self):
        before = self.client.get(self.url).json()
        for reply, status in [('{"invalid":true}', 200), ({"operations": []}, 401)]:
            self.reply, self.upstream_status = reply, status
            response = self.preview()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(self.settings.llm_api_key, response.text)
            self.assertEqual(self.client.get(self.url).json(), before)
        self.assertFalse(self.calls)

    def test_code_and_widget_requests_cannot_bypass_phase_gate(self):
        before = self.client.get(self.url).json()
        for text in ["给第二天添加HTML与JavaScript", "执行代码", "增加物品清单"]:
            response = self.preview(text)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.client.get(self.url).json(), before)
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
