"""SQLite snapshots with atomic version checks; imports existing local JSON once."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading

from fastapi import HTTPException


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "travel.sqlite3"
        self.lock = threading.RLock()
        # Previews deliberately expire on restart. Deploy a single app worker.
        self.proposals: dict[str, dict] = {}
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, version INTEGER NOT NULL, document TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS trip_versions (trip_id TEXT NOT NULL, version INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(trip_id, version))")
            for path in self.directory.glob("*.json"):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(document.get("id"), str) or "_undo" not in document:
                        continue
                    connection.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)", (document["id"], document["version"], json.dumps(document, ensure_ascii=False)))
                except (ValueError, OSError, KeyError, TypeError):
                    continue

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, session_id: str) -> dict:
        with self._connect() as connection:
            record = connection.execute("SELECT document FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if record is None:
            raise HTTPException(404, "未找到这个行程，或你没有访问权限。")
        return json.loads(record[0])

    def save(self, session: dict, version_record: dict | None = None):
        document = json.dumps(session, ensure_ascii=False, allow_nan=False)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute("SELECT version, document FROM sessions WHERE id = ?", (session["id"],)).fetchone()
                if previous is None:
                    if session["version"] != 1:
                        raise HTTPException(409, "会话已变化，请重新载入后再操作。")
                    connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session["id"], session["version"], document))
                else:
                    from .versioning import checkpoint
                    connection.execute("INSERT OR IGNORE INTO trip_versions VALUES (?, ?, ?)",
                                       (session["id"], previous[0], json.dumps(checkpoint(json.loads(previous[1])), ensure_ascii=False, allow_nan=False)))
                    cursor = connection.execute("UPDATE sessions SET version = ?, document = ? WHERE id = ? AND version = ?", (session["version"], document, session["id"], session["version"] - 1))
                    if cursor.rowcount != 1:
                        raise HTTPException(409, "行程已在另一窗口更新，请重新载入后再操作。")
                from .versioning import make_record
                record = version_record or make_record(json.loads(previous[1]) if previous else None, session)
                if record["tripId"] != session["id"] or record["version"] != session["version"]:
                    raise HTTPException(400, "版本记录与当前行程不一致。")
                connection.execute("INSERT INTO trip_versions VALUES (?, ?, ?)",
                                   (session["id"], session["version"], json.dumps(record, ensure_ascii=False, allow_nan=False)))
        except sqlite3.Error as exc:
            raise HTTPException(503, "行程存储暂时不可用，请稍后重试。") from exc

    def claim_legacy(self, session_id: str, owner: str) -> dict:
        """Bind an old desktop session once, without changing its business version."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT document FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "未找到这个行程，或你没有访问权限。")
            session = json.loads(row[0])
            if session.get("_owner") not in {None, owner}:
                raise HTTPException(404, "未找到这个行程，或你没有访问权限。")
            session["_owner"] = owner
            connection.execute("UPDATE sessions SET document = ? WHERE id = ?", (json.dumps(session, ensure_ascii=False), session_id))
            return deepcopy(session)

    def ensure_checkpoint(self, session: dict):
        """Add only the currently known legacy version; never invent past snapshots."""
        from .versioning import checkpoint
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT version FROM sessions WHERE id=?", (session["id"],)).fetchone()
            if not row or row[0] != session["version"]:
                raise HTTPException(409, "创建历史检查点时行程已变化，请重新载入。")
            connection.execute("INSERT OR IGNORE INTO trip_versions VALUES (?, ?, ?)",
                               (session["id"], session["version"], json.dumps(checkpoint(session), ensure_ascii=False, allow_nan=False)))

    def versions(self, session_id: str):
        with self._connect() as connection:
            rows = connection.execute("SELECT record FROM trip_versions WHERE trip_id=? ORDER BY version DESC", (session_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def version(self, session_id: str, version: int):
        with self._connect() as connection:
            row = connection.execute("SELECT record FROM trip_versions WHERE trip_id=? AND version=?", (session_id, version)).fetchone()
        if not row:
            raise HTTPException(404, {"code": "VERSION_NOT_FOUND", "message": f"行程中不存在已保存的版本 v{version}。旧历史只有摘要时不能恢复。"})
        return json.loads(row[0])
