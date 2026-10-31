"""Stable content revisions shared by the server and local startup launcher.

The launcher can call ``python -m backend.revision`` without importing the app,
opening the session database, or reading API keys.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

APP_VERSION = "0.6.5-phase4"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT_FILES = (
    "index.html", "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    "tsconfig.json", "vite.config.ts",
)


def fingerprint(root: Path | str, relative_paths: Iterable[Path | str]) -> str:
    """Hash POSIX relative names and exact file bytes in sorted name order."""
    root = Path(root).resolve()
    names = []
    for relative in relative_paths:
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Fingerprint paths must stay relative to the project root")
        names.append(relative.as_posix())
    digest = hashlib.sha256()
    for name in sorted(names):
        source = root / name
        source.resolve().relative_to(root)
        digest.update(name.encode("utf-8") + b"\0" + source.read_bytes() + b"\0")
    return digest.hexdigest()


def backend_paths(root: Path | str = PROJECT_ROOT) -> list[str]:
    """Runtime Python sources and dependency requirements, excluding tests/data."""
    root = Path(root)
    names = []
    for source in (root / "backend").rglob("*.py"):
        relative = source.relative_to(root)
        if source.is_file() and not source.is_symlink() and not source.name.startswith("test_") and not {"__pycache__", ".runtime"}.intersection(relative.parts):
            names.append(relative.as_posix())
    if (root / "backend/ranking_weights.json").is_file():
        names.append("backend/ranking_weights.json")
    requirements = root / "backend" / "requirements.txt"
    if requirements.is_file():
        names.append("backend/requirements.txt")
    return sorted(names)


def frontend_paths(root: Path | str = PROJECT_ROOT) -> list[str]:
    """Frontend inputs; generated dist, node_modules and runtime data are absent."""
    root = Path(root)
    names = [name for name in FRONTEND_ROOT_FILES if (root / name).is_file()]
    for directory in ("src", "public", "scripts"):
        for source in (root / directory).rglob("*"):
            relative = source.relative_to(root)
            if source.is_file() and not source.is_symlink() and not {"__pycache__", ".vite-temp"}.intersection(relative.parts):
                names.append(relative.as_posix())
    return sorted(names)


def server_revision(root: Path | str = PROJECT_ROOT) -> str:
    return fingerprint(root, backend_paths(root))


def frontend_revision(root: Path | str = PROJECT_ROOT) -> str:
    return fingerprint(root, frontend_paths(root))


def revision_info(root: Path | str = PROJECT_ROOT) -> dict[str, str]:
    return {"version": APP_VERSION, "serverRevision": server_revision(root), "frontendRevision": frontend_revision(root)}


if __name__ == "__main__":
    print(json.dumps(revision_info(), ensure_ascii=True, separators=(",", ":")))
