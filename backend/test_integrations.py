"""Local runtime service-integration settings: python -m unittest backend.test_integrations -v."""

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as module


class IntegrationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        data_dir = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            "TRAVEL_GENUI_DATA_DIR": str(data_dir),
            "TRAVEL_ENV_FILE": str(data_dir / "absent.env"),
            "AGENT_MODE": "demo",
            "CANDIDATE_AGENT_MODE": "mock",
            "LLM_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "LLM_BASE_URL": "https://api.deepseek.com",
            "LLM_MODEL": "deepseek-flash",
            "AMAP_KEY": "",
        })
        self.environment.start()
        self.client = TestClient(module.app)
        self.path = data_dir / "integrations.json"

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        self.temporary.cleanup()

    def test_model_configuration_is_private_persistent_and_normalizes_full_endpoint(self):
        secret = "model-secret-that-must-not-be-returned"
        initial = self.client.get("/api/integrations")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertFalse(initial.json()["model"]["configured"])

        response = self.client.put("/api/integrations/model", json={
            "apiKey": secret,
            "baseUrl": "https://models.example.test/v1/chat/completions/",
            "model": "travel-model-v1",
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["model"]["configured"])
        self.assertEqual(body["model"]["baseUrl"], "https://models.example.test/v1")
        self.assertEqual(body["model"]["model"], "travel-model-v1")
        self.assertTrue(body["model"]["runtimeOverride"])
        self.assertNotIn(secret, response.text)

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["LLM_API_KEY"], secret)
        self.assertEqual(saved["AGENT_MODE"], "llm")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        status = self.client.get("/api/agent/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertTrue(status.json()["configured"])
        self.assertNotIn(secret, status.text)

        removed = self.client.delete("/api/integrations/model")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["model"]["configured"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["AGENT_MODE"], "demo")

    def test_amap_configuration_never_returns_key_and_can_be_removed(self):
        secret = "amap-secret-that-must-not-be-returned"
        response = self.client.put("/api/integrations/amap", json={
            "apiKey": secret,
            "baseUrl": "https://restapi.amap.com",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["amap"]["configured"])
        self.assertEqual(response.json()["amap"]["baseUrl"], "https://restapi.amap.com")
        self.assertNotIn(secret, response.text)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["AMAP_KEY"], secret)

        removed = self.client.delete("/api/integrations/amap")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["amap"]["configured"])
        self.assertNotIn(secret, removed.text)

    def test_integration_settings_reject_non_local_host_and_invalid_addresses(self):
        denied = self.client.get("/api/integrations", headers={"host": "travel.example.com"})
        self.assertEqual(denied.status_code, 403, denied.text)

        invalid = self.client.put("/api/integrations/model", json={
            "apiKey": "secret",
            "baseUrl": "https://user:password@models.example.test/v1",
            "model": "travel-model-v1",
        })
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
