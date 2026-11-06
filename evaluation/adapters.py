"""Strict, code-free model-output contracts shared by formal A-D adapters.

Network invocation and state application intentionally live outside this module.
Parsing a response does not imply that it satisfies task semantics.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from fastapi import HTTPException

from backend.trip_document import validate_document_schema

try:
    from .prompt_bundle import load_bundle
except ImportError:  # direct script/import from evaluation directory
    from prompt_bundle import load_bundle


MAX_RESPONSE_BYTES = 1_000_000
MAX_MESSAGE_CHARACTERS = 2_000
STRATEGIES = ("minimal_change", "efficiency_first", "experience_first")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def _parse(text: str) -> dict:
    if not isinstance(text, str) or not text:
        raise ValueError("model response must be a non-empty string")
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("model response exceeds 1 MB")
    value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("model response must be one JSON object")
    return value


def _error(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"code", "message"}:
        raise ValueError("error must contain only code and message")
    if not all(isinstance(value[key], str) and value[key] for key in ("code", "message")):
        raise ValueError("error code and message must be non-empty strings")
    if len(value["code"]) > 100 or len(value["message"]) > MAX_MESSAGE_CHARACTERS:
        raise ValueError("error code/message exceeds the registered length limit")
    return {"kind": "error", "error": deepcopy(value)}


def _patch(value: object, allowed_paths: set[str]) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError("patch must contain 1-16 operations")
    result = []
    seen: set[str] = set()
    for operation in value:
        if not isinstance(operation, dict) or set(operation) != {"op", "path", "value"}:
            raise ValueError("patch operations require exactly op/path/value")
        if operation["op"] != "replace":
            raise ValueError("only replace operations are allowed")
        path = operation["path"]
        if not isinstance(path, str) or path not in allowed_paths:
            raise ValueError("patch path is outside the registered allowlist")
        if path in seen:
            raise ValueError("a patch cannot write the same path twice")
        if not isinstance(operation["value"], str) or len(operation["value"]) > 2000:
            raise ValueError("patch values must be strings of at most 2000 characters")
        seen.add(path)
        result.append(deepcopy(operation))
    return result


def parse_model_output(text: str, method: str, allowed_paths: list[str] | None = None) -> dict:
    if method not in {"A", "B", "C", "D"}:
        raise ValueError("method must be A, B, C or D")
    value = _parse(text)
    if set(value) == {"error"}:
        return _error(value["error"])

    decision = value.get("decision")
    message = value.get("message")
    if not isinstance(message, str) or not message:
        raise ValueError("normal responses require a non-empty message")
    if len(message) > MAX_MESSAGE_CHARACTERS:
        raise ValueError("message exceeds the registered length limit")
    if decision in {"clarify", "reject"}:
        if set(value) != {"decision", "message"}:
            raise ValueError("clarify/reject responses contain only decision and message")
        return {"kind": "decision", "decision": decision, "message": message}

    allowed = set(allowed_paths or [])
    if method in {"A", "B"}:
        if decision != "edit" or set(value) != {"decision", "message", "document"} or not isinstance(value["document"], dict):
            raise ValueError("A/B responses require edit/message/document or clarify/reject")
        try:
            validate_document_schema(value["document"])
        except HTTPException as error:
            raise ValueError("TripDocument schema validation failed") from error
        return {"kind": "document", "decision": "edit", "message": message, "document": deepcopy(value["document"])}

    if method == "C":
        if not allowed:
            raise ValueError("C edit parsing requires a non-empty path allowlist")
        if decision != "edit" or set(value) != {"decision", "message", "patch"}:
            raise ValueError("C responses require edit/message/patch or clarify/reject")
        return {"kind": "patch", "decision": "edit", "message": message, "patch": _patch(value["patch"], allowed)}

    if decision == "restore":
        if set(value) not in ({"decision", "message", "targetVersion"}, {"decision", "message", "targetVersion", "postRestoreCandidates"}):
            raise ValueError("D restore contains decision/message/targetVersion and optional postRestoreCandidates")
        if not isinstance(value["targetVersion"], int) or isinstance(value["targetVersion"], bool) or value["targetVersion"] < 1:
            raise ValueError("targetVersion must be a positive integer")
        candidates = []
        if "postRestoreCandidates" in value:
            candidates = _candidate_list(value["postRestoreCandidates"], allowed)
        return {"kind": "tool_action", "decision": "restore", "message": message, "targetVersion": value["targetVersion"], "postRestoreCandidates": candidates}
    if decision == "update_constraint":
        if set(value) != {"decision", "message", "constraint"}:
            raise ValueError("D constraint update contains only decision/message/constraint")
        constraint = value["constraint"]
        if not isinstance(constraint, dict) or set(constraint) != {"kind", "value", "confirmed"}:
            raise ValueError("constraint requires kind/value/confirmed")
        if constraint["kind"] != "max_budget" or constraint["confirmed"] is not True:
            raise ValueError("only explicitly confirmed max_budget updates are registered")
        amount = constraint["value"]
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 100 <= amount <= 100000):
            raise ValueError("max_budget value must be null or between 100 and 100000")
        return {"kind": "tool_action", "decision": "update_constraint", "message": message, "constraint": deepcopy(constraint)}

    if decision != "edit" or set(value) != {"decision", "message", "candidates"}:
        raise ValueError("D responses require a registered decision envelope")
    if not allowed:
        raise ValueError("D edit parsing requires a non-empty path allowlist")
    candidates = _candidate_list(value["candidates"], allowed)
    return {"kind": "candidates", "decision": "edit", "message": message, "candidates": candidates}


def _candidate_list(value: object, allowed: set[str]) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise ValueError("D responses require 1-3 candidates")
    candidates = []
    seen_strategies: set[str] = set()
    for candidate in value:
        if not isinstance(candidate, dict) or set(candidate) != {"strategy", "patch"}:
            raise ValueError("D candidates require exactly strategy and patch")
        strategy = candidate["strategy"]
        if strategy not in STRATEGIES or strategy in seen_strategies:
            raise ValueError("D candidate strategies must be registered and unique")
        seen_strategies.add(strategy)
        candidates.append({"strategy": strategy, "patch": _patch(candidate["patch"], allowed)})
    return candidates


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_prompt(prompt_directory: Path, method: str, context: dict) -> str:
    if method not in {"A", "B", "C", "D"}:
        raise ValueError("method must be A, B, C or D")
    required = {"conversation_history", "current_document", "declared_controls", "version_history", "catalog", "current_instruction"}
    missing = required - context.keys()
    if missing:
        raise ValueError(f"missing shared prompt context: {sorted(missing)}")
    bundle = load_bundle(prompt_directory)
    common = bundle["files"]["common.txt"]
    for name in sorted(required):
        common = common.replace("{" + name + "}", _json(context[name]))
    specific = bundle["files"][method + ".txt"]
    if method in {"C", "D"}:
        allowed = context.get("allowed_paths")
        if not isinstance(allowed, list):
            raise ValueError("C/D prompts require allowed_paths")
        specific = specific.replace("{allowed_paths}", _json(allowed))
    if method == "D":
        if "locks_and_constraints" not in context:
            raise ValueError("D prompts require locks_and_constraints")
        specific = specific.replace("{locks_and_constraints}", _json(context["locks_and_constraints"]))
    return common.rstrip() + "\n\n" + specific.strip() + "\n"
