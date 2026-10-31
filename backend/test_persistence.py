from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException

from backend.persistence import Store


class PersistenceTests(unittest.TestCase):
    def test_compare_and_swap_prevents_two_stores_losing_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Store(Path(directory)), Store(Path(directory))
            first.save({"id": "x", "version": 1, "_owner": "first", "_undo": [], "note": "old"})
            current = first.get("x")
            stale = second.get("x")
            current.update(version=2, note="new")
            first.save(current)
            stale.update(version=2, note="overwrite")
            with self.assertRaises(HTTPException) as error:
                second.save(stale)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(second.get("x")["note"], "new")

    def test_legacy_json_is_imported_without_overwriting_new_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "legacy.json").write_text(json.dumps({"id": "legacy", "version": 1, "_undo": [], "note": "legacy"}), encoding="utf-8")
            first = Store(path)
            claimed = first.claim_legacy("legacy", "visitor")
            self.assertEqual(claimed["_owner"], "visitor")
            current = deepcopy(claimed)
            current.update(version=2, note="edited")
            first.save(current)
            second = Store(path)
            self.assertEqual(second.get("legacy")["note"], "edited")
            with self.assertRaises(HTTPException):
                second.claim_legacy("legacy", "different-visitor")
