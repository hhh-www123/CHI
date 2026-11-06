#!/usr/bin/env python3
"""Validate and hash the frozen-input prompt bundle without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIRECTORY = ROOT / "evaluation" / "prompts" / "v1"
COMMON_FIELDS = {"conversation_history", "current_document", "declared_controls", "version_history", "catalog", "current_instruction"}
METHOD_FIELDS = {
    "A.txt": set(),
    "B.txt": set(),
    "C.txt": {"allowed_paths"},
    "D.txt": {"allowed_paths", "locks_and_constraints"},
}


def _placeholders(text: str) -> set[str]:
    result: set[str] = set()
    index = 0
    while index < len(text):
        left = text.find("{", index)
        if left < 0:
            break
        right = text.find("}", left + 1)
        if right < 0:
            raise ValueError("unclosed prompt placeholder")
        name = text[left + 1:right]
        if name and all(character.isalnum() or character == "_" for character in name):
            result.add(name)
        index = right + 1
    return result


def load_bundle(directory: Path) -> dict:
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schemaVersion") != "1.0" or metadata.get("status") not in {"draft", "frozen"}:
        raise ValueError("prompt metadata requires schemaVersion 1.0 and draft/frozen status")
    expected_files = ["common.txt", *METHOD_FIELDS]
    if metadata.get("files") != expected_files:
        raise ValueError(f"prompt metadata files must be {expected_files}")
    texts = {name: (directory / name).read_text(encoding="utf-8") for name in expected_files}
    if _placeholders(texts["common.txt"]) != COMMON_FIELDS:
        raise ValueError("common prompt placeholders do not match the registered shared inputs")
    for name, expected in METHOD_FIELDS.items():
        if _placeholders(texts[name]) != expected:
            raise ValueError(f"{name} placeholders do not match its method contract")
    payload = {"metadata": metadata, "files": texts}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "version": metadata["version"],
        "status": metadata["status"],
        "hash": hashlib.sha256(encoded).hexdigest(),
        "files": texts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    bundle = load_bundle(args.directory)
    print(json.dumps({
        "valid": True,
        "version": bundle["version"],
        "status": bundle["status"],
        "hash": bundle["hash"],
        "files": sorted(bundle["files"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
