"""Visitor ownership and browser boundary regression tests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import access
from backend import app as module
from backend.config import Settings


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous = module.store
        module.store = module.Store(Path(self.temporary.name))
        self.settings = Settings(agent_mode="demo", cookie_secure=True, requests_per_minute=120)
        self.access_settings = patch.object(access, "get_settings", return_value=self.settings)
        self.module_settings = patch.object(module, "get_settings", return_value=self.settings)
        self.access_settings.start()
        self.module_settings.start()
        self.first = TestClient(module.app, base_url="https://testserver")
        self.second = TestClient(module.app, base_url="https://testserver")

    def tearDown(self):
        self.first.close()
        self.second.close()
        self.access_settings.stop()
        self.module_settings.stop()
        module.store = self.previous
        self.temporary.cleanup()

    def test_two_visitors_cannot_read_or_write_each_others_trip_and_cookie_is_private(self):
        response = self.first.post("/api/sessions")
        self.assertEqual(response.status_code, 200, response.text)
        cookie = response.headers["set-cookie"]
        for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            self.assertIn(attribute, cookie)
        session = response.json()
        self.assertNotIn("_owner", session)
        url = f"/api/sessions/{session['id']}"
        self.assertEqual(self.first.get(url).status_code, 200)
        self.second.get("/api/health")
        self.assertNotEqual(self.first.cookies.get(access.COOKIE), self.second.cookies.get(access.COOKIE))
        self.assertEqual(self.second.get(url).status_code, 404)
        foreign_write = self.second.patch(url + "/direct", json={"baseVersion": 1, "kind": "note", "targetRegionId": "day-2", "value": "篡改"})
        self.assertEqual(foreign_write.status_code, 404)
        self.assertEqual(self.first.get(url).json()["tripState"]["days"]["day2"]["note"], "")

    def test_cross_origin_is_rejected_and_undo_preserves_owner(self):
        session = self.first.post("/api/sessions").json()
        url = f"/api/sessions/{session['id']}"
        body = {"baseVersion": 1, "kind": "note", "targetRegionId": "day-2", "value": "我自己的备注"}
        attack = self.first.patch(url + "/direct", json=body, headers={"Origin": "https://untrusted.invalid"})
        self.assertEqual(attack.status_code, 403)
        self.assertEqual(self.first.get(url).json()["version"], 1)
        changed = self.first.patch(url + "/direct", json=body, headers={"Origin": "https://testserver"})
        self.assertEqual(changed.status_code, 200, changed.text)
        owner = module.store.get(session["id"])["_owner"]
        undone = self.first.post(url + "/undo", json={"baseVersion": changed.json()["version"]})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()["tripState"]["days"]["day2"]["note"], "")
        self.assertEqual(module.store.get(session["id"])["_owner"], owner)
        self.assertEqual(self.second.get(url).status_code, 404)

    def test_old_json_is_adopted_once_and_chunked_oversized_body_is_rejected(self):
        legacy_id = "a" * 32
        legacy = {"id": legacy_id, "version": 1, **module.initial_payload(), "history": [], "_undo": []}
        (Path(self.temporary.name) / "old.json").write_text(json.dumps(legacy), encoding="utf-8")
        module.store = module.Store(Path(self.temporary.name))
        url = f"/api/sessions/{legacy_id}"
        first = self.first.get(url)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["version"], 1)
        self.assertTrue(module.store.get(legacy_id)["_owner"])
        self.assertEqual(self.second.get(url).status_code, 404)
        response = self.first.post("/api/sessions", content=iter([b"x" * 40000, b"y" * 40000]), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
