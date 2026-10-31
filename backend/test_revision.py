"""Revision and browser freshness checks, with isolated session storage.

Run: python -m unittest backend.test_revision -v
"""
from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.testclient import TestClient

from backend.revision import (
    APP_VERSION, backend_paths, fingerprint, frontend_paths,
    frontend_revision, revision_info, server_revision,
)


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="travel-revision-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("backend/app.py", b"runtime = 1\n")
        self.write("backend/requirements.txt", b"fastapi\n")
        self.write("index.html", b"<html></html>")
        self.write("src/main.ts", b"export {}\n")
        self.write("package.json", b"{}\n")

    def write(self, name, content):
        destination = self.root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def test_fingerprint_uses_sorted_posix_names_exact_bytes_and_null_separators(self):
        self.write("src/景点.ts", b"\x00\xff\r\n")
        paths = ["src/景点.ts", "src/main.ts"]
        expected = hashlib.sha256()
        for name in sorted(paths):
            expected.update(name.encode("utf-8") + b"\0" + (self.root / name).read_bytes() + b"\0")
        self.assertEqual(fingerprint(self.root, paths), expected.hexdigest())
        self.assertEqual(fingerprint(self.root, reversed(paths)), expected.hexdigest())
        with self.assertRaises(ValueError):
            fingerprint(self.root, ["../outside.py"])

    def test_backend_source_and_requirements_changes_update_revision(self):
        before = server_revision(self.root)
        self.write("backend/app.py", b"runtime = 2\n")
        self.assertNotEqual(server_revision(self.root), before)
        before = server_revision(self.root)
        self.write("backend/nested/feature.py", b"enabled = True\n")
        self.assertNotEqual(server_revision(self.root), before)
        before = server_revision(self.root)
        self.write("backend/requirements.txt", b"fastapi\nhttpx\n")
        self.assertNotEqual(server_revision(self.root), before)
        self.assertEqual(backend_paths(self.root), sorted(backend_paths(self.root)))

    def test_runtime_env_test_and_cache_changes_leave_backend_revision_unchanged(self):
        before = server_revision(self.root)
        for name in (".env", "backend/.runtime/travel.sqlite3", "backend/.runtime/old.py", "backend/__pycache__/cached.py", "backend/test_revision.py", "backend/nested/test_other.py", "backend/app.pyc", ".runtime/server.pid"):
            self.write(name, b"temporary data\n")
        self.assertEqual(server_revision(self.root), before)

    def test_frontend_inputs_include_optional_scripts_and_public_assets(self):
        before = frontend_revision(self.root)
        for name in ("pnpm-lock.yaml", "pnpm-workspace.yaml", "tsconfig.json", "vite.config.ts", "public/map.svg", "scripts/build.mjs"):
            self.write(name, b"input\n")
            current = frontend_revision(self.root)
            self.assertNotEqual(current, before, name)
            before = current
        self.assertEqual(frontend_paths(self.root), sorted(frontend_paths(self.root)))
        self.assertEqual(revision_info(self.root)["frontendRevision"], before)
        self.assertEqual(revision_info(self.root)["version"], APP_VERSION)

    def test_frontend_ignores_generated_outputs_runtime_and_temporary_inputs(self):
        before = frontend_revision(self.root)
        for name in ("dist/index.html", "dist/build-info.json", "node_modules/example.js", ".env", ".runtime/log.txt", "src/__pycache__/compiled.py", "public/.vite-temp/temp.js", "scripts/.vite-temp/config.js"):
            self.write(name, b"generated\n")
        self.assertEqual(frontend_revision(self.root), before)


class HealthFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="travel-health-revision-")
        # Importing app constructs a Store; redirect it before a first import.
        with patch.dict(os.environ, {"TRAVEL_GENUI_DATA_DIR": cls.temporary.name}):
            cls.module = importlib.import_module("backend.app")
        cls.store_patch = patch.object(cls.module, "store", cls.module.Store(Path(cls.temporary.name)))
        cls.store_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.store_patch.stop()
        cls.temporary.cleanup()

    def test_health_returns_frozen_startup_revision_without_rehashing(self):
        with patch.object(self.module, "server_revision", side_effect=AssertionError("health must not rehash changed disk files")), patch.object(self.module.agent, "status", return_value={"mode": "demo"}):
            with TestClient(self.module.app) as client:
                result = client.get("/api/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["serverRevision"], self.module.SERVER_REVISION)
        self.assertEqual(result.json()["version"], APP_VERSION)
        self.assertEqual(len(result.json()["serverRevision"]), 64)
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_external_health_does_not_expose_project_process_or_revision(self):
        request = Request({"type": "http", "client": ("203.0.113.7", 12345), "headers": []})
        with patch.object(self.module.agent, "status", return_value={"mode": "demo"}):
            result = self.module.health(request)
        for name in ("projectRoot", "processId", "serverRevision"):
            self.assertNotIn(name, result)

    def test_real_app_root_document_disables_cache(self):
        with TestClient(self.module.app) as client:
            result = client.get("/")
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_html_documents_disable_cache_while_hashed_assets_keep_existing_policy(self):
        site = FastAPI()
        site.middleware("http")(self.module.refresh_frontend_documents)

        @site.get("/index.html")
        def document():
            return HTMLResponse("<html>current app</html>", headers={"Cache-Control": "max-age=3600"})

        @site.get("/assets/index-abc123.js")
        def asset():
            return Response("console.log('current')", media_type="application/javascript", headers={"Cache-Control": "public, max-age=31536000, immutable"})

        with TestClient(site) as client:
            self.assertEqual(client.get("/index.html").headers["cache-control"], "no-store")
            self.assertEqual(client.get("/assets/index-abc123.js").headers["cache-control"], "public, max-age=31536000, immutable")


if __name__ == "__main__":
    unittest.main()
