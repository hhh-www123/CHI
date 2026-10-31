"""Phase 2 server-owned candidate review, read-only previews and atomic commit.

Uses the existing session URL boundary so anonymous ownership, origin checks and
rate limits apply. Network generation happens outside the Store lock. Candidates
live only in the bounded server cache and expire on restart or TTL.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException, Request
import logging
from .candidate_ranking import evaluate, diversity
from .protocol_adapter import surface
from .stream_adapter import response as stream_response
_LOG = logging.getLogger("travel.candidates")
_LOG.setLevel(logging.INFO)
if not _LOG.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOG.addHandler(handler)
_LOG.propagate=False
from jsonschema import Draft202012Validator
from pydantic import Field, ValidationError

from . import candidate_agent
from .config import get_settings
from .editor_api import VersionedEditor
from .patch_guard import decode_pointer, resolve_scope, validate_patch
from .trip_document import to_trip_document


class CandidatesBody(VersionedEditor):
    selectedComponentIds: list[str] = Field(max_length=1)
    instruction: str = Field(min_length=1, max_length=2000)
    fields: list[Literal["description", "startTime", "placeId"]] | None = Field(default=None, min_length=1, max_length=3)
    agentMode: Literal["mock", "llm"] | None = None


class CandidateCommitBody(VersionedEditor):
    candidateId: str = Field(min_length=1, max_length=100)


_AGENT_VALIDATOR = Draft202012Validator(candidate_agent.AgentCandidate.model_json_schema())


def _failure(strategy, code, message):
    return {"strategy": strategy if strategy in candidate_agent.STRATEGIES else "unknown",
            "code": code, "message": message}


def _times(document, eligible):
    return {aid: candidate_agent._minutes(document["entities"]["activities"][aid]["startTime"])
            for aid in eligible}


def review_candidates(snapshot: dict, selected: list[str], fields, proposals: list) -> tuple[list, list]:
    """All candidates start from snapshot; model-provided legality is never used.

    Dedup compares full final clock vectors (not strategy prose). Identical final
    values are removed. A near-identical clock vector (<=5 min at every activity)
    is also removed, conservatively collapsing descriptions-only alternatives.
    """
    accepted, rejected = [], []
    seen_strategies = set()
    for proposal in proposals:
        strategy = proposal.get("strategy") if isinstance(proposal, dict) else "unknown"
        try:
            if list(_AGENT_VALIDATOR.iter_errors(proposal)):
                raise ValueError("schema")
            parsed = candidate_agent.AgentCandidate.model_validate(proposal)
        except (ValueError, TypeError, ValidationError):
            rejected.append(_failure(strategy, "CANDIDATE_SCHEMA_INVALID", "模型候选必须只有 strategy 和受限 patch；不接受自评、分数、权限或代码字段。"))
            continue
        if parsed.strategy in seen_strategies:
            rejected.append(_failure(strategy, "DUPLICATE_STRATEGY", "同一策略只保留一个合格候选。"))
            continue
        operations = [operation.model_dump() for operation in parsed.patch]
        try:
            checked = validate_patch(snapshot, selected, operations, fields)
        except HTTPException as error:
            detail = error.detail
            rejected.append(_failure(strategy, detail.get("code", "PATCH_REJECTED") if isinstance(detail, dict) else "PATCH_REJECTED",
                                     detail.get("message", "候选未通过权限与不变量检查。") if isinstance(detail, dict) else str(detail)))
            continue
        final_times = _times(checked["draft"], checked["effectiveScope"]["eligibleActivityIds"])
        similar = False
        for previous in accepted:
            previous_times = _times(previous["checked"]["draft"], checked["effectiveScope"]["eligibleActivityIds"])
            same_places = all(checked["draft"]["entities"]["activities"][aid]["placeId"] == previous["checked"]["draft"]["entities"]["activities"][aid]["placeId"] for aid in final_times)
            if checked["draft"] == previous["checked"]["draft"] or (same_places and all(abs(final_times[aid] - previous_times[aid]) <= 5 for aid in final_times)):
                similar = True
                break
        if similar:
            rejected.append(_failure(strategy, "SIMILAR_CANDIDATE", "与已有候选的实际时间安排相同或差异不超过5分钟；仅更换描述文案不足以成为独立方案。"))
            continue
        accepted.append({"strategy": parsed.strategy, "patch": operations, "checked": checked})
        seen_strategies.add(parsed.strategy)
    # Keep A/B/C order independent of model output order; no model scores used.
    accepted.sort(key=lambda item: candidate_agent.STRATEGIES.index(item["strategy"]))
    return accepted[:3], rejected


def _differences(checked):
    result = []
    for path in checked["changedPaths"]:
        tokens = decode_pointer(path)
        if tokens[:2] == ("entities", "activities"):
            _, _, aid, field = tokens
            before = checked["document"]["entities"]["activities"][aid]
            after = checked["draft"]["entities"]["activities"][aid]
        else:
            _, _, card, field = tokens
            aid = card.removeprefix("card-")
            before = checked["document"]["ui"]["components"][card]
            after = checked["draft"]["ui"]["components"][card]
        title = checked["document"]["entities"]["activities"][aid]["title"]
        result.append({"path": path, "activityId": aid, "title": title, "field": field,
                       "before": before[field], "after": after[field]})
    return result


def _candidate_rationale(item):
    changed = set(item["checked"]["changedPaths"])
    counts = {"placeId": 0, "startTime": 0, "description": 0}
    for operation in item["patch"]:
        if operation["path"] in changed:
            field = operation["path"].rsplit("/", 1)[-1]
            counts[field] += 1
    facts = []
    if counts["placeId"]:
        facts.append(f"替换{counts['placeId']}个当前真实目录中的景点")
    if counts["startTime"]:
        facts.append(f"调整{counts['startTime']}个开始时间")
    if counts["description"]:
        facts.append(f"改写{counts['description']}条景点说明")
    if item["checked"]["derivedChangedPaths"]:
        facts.append("相关地点信息或时段归属由服务端按实际改动自动同步")
    return "；".join(facts) + "。仅修改选区内获准内容，其他日期和锁定保持；效率策略不代表已核实真实交通距离。"


def install_candidate_api(app, host):
    def read(session_id, version=None):
        session = host.normalize_session(host.store.get(session_id))
        if version is not None:
            host.require_version(session, version)
        return session

    @app.get("/api/edits/status")
    def status():
        settings = get_settings()
        return {"phase": "Phase 4", "defaultMode": settings.candidate_agent_mode,
                "llmConfigured": bool(settings.llm_api_key and settings.llm_model),
                "model": settings.llm_model, "provider": urlsplit(settings.llm_base_url).hostname or "",
                "timeoutSeconds": settings.llm_timeout, "candidateTtlSeconds": settings.candidate_ttl_seconds}

    def build_candidates(session_id: str, body: CandidatesBody, progress=lambda name,value=None: None):
        started = time.perf_counter()
        progress("scope.started")
        settings = get_settings()
        mode = body.agentMode or settings.candidate_agent_mode
        instruction = body.instruction.strip()
        if not instruction:
            raise HTTPException(422, {"code": "EMPTY_INSTRUCTION", "message": "请输入要在选区内修改的要求。"})
        with host.store.lock:
            snapshot = deepcopy(read(session_id, body.baseVersion))
            effective = resolve_scope(snapshot, body.selectedComponentIds, body.fields)
            if effective["selectedLocked"]:
                raise HTTPException(423, {"code": "LOCKED_COMPONENT", "message": "选区或上级组件已锁定，请先解锁或重新选择。"})
            if not effective["allowedPaths"]:
                raise HTTPException(403, {"code": "SCOPE_DENIED", "message": "当前选区没有未锁定的活动字段；请选择有活动的日期、时段或景点卡片。"})
        progress("scope.validated", {"baseVersion": snapshot["version"]})
        progress("generation.started", {"mode": mode})
        # No store lock, mutation, map call or database write while invoking Agent.
        proposals = candidate_agent.generate(deepcopy(snapshot), instruction, deepcopy(effective), mode, settings)
        progress("generation.finished")
        progress("validation.started")
        accepted, rejected = review_candidates(snapshot, body.selectedComponentIds, body.fields, proposals)
        attempts = 1
        if mode == "llm" and len(accepted) < 3:
            progress("generation.retry", {"reason": "不足三个不同且合法的候选，原范围内补充一次"})
            more = candidate_agent.generate(deepcopy(snapshot), instruction, deepcopy(effective), mode, settings)
            proposals += more
            unique = {}
            for proposal in proposals:
                unique[json.dumps(proposal, sort_keys=True, ensure_ascii=False)] = proposal
            proposals = list(unique.values())
            accepted, rejected = review_candidates(snapshot, body.selectedComponentIds, body.fields, proposals)
            attempts += 1
        progress("validation.finished", {"accepted": len(accepted), "proposed": len(proposals)})
        with host.store.lock:
            current = read(session_id, body.baseVersion)
            # A versioned state must be immutable. Also guard an unexpected
            # unversioned authoritative change before caching any candidate.
            if to_trip_document(current) != to_trip_document(snapshot) or current["persistedConstraints"] != snapshot["persistedConstraints"]:
                raise HTTPException(409, {"code": "VERSION_CONFLICT", "message": "生成期间行程或约束已变化，请重新生成候选。"})
            if not accepted:
                reasons = "；".join(item["message"] for item in rejected[:3])
                raise HTTPException(422, {"code": "NO_VALID_CANDIDATES", "message": "没有合格候选，正式状态未变化。" + reasons})
            request_id = "req-" + uuid4().hex
            ttl = settings.candidate_ttl_seconds
            expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)
            expires_at = expiry.isoformat().replace("+00:00", "Z")
            created = time.monotonic()
            results = []
            for item in accepted:
                identifier = "p2-" + uuid4().hex
                checked = item["checked"]
                entry = {"kind": "phase2_candidate", "sessionId": session_id, "requestId": request_id,
                         "candidateId": identifier, "baseVersion": snapshot["version"],
                         "selectedComponentIds": deepcopy(body.selectedComponentIds), "fields": deepcopy(body.fields),
                         "patch": deepcopy(item["patch"]), "strategy": item["strategy"],
                         "instruction": instruction, "generationMode": mode,
                         "createdMonotonic": created, "expiresMonotonic": created + ttl,
                         "expiresAt": expires_at, "validationReport": {"valid": True, **checked["validationReport"]},
                         "changedPaths": deepcopy(checked["changedPaths"]),
                         "effectiveScope": deepcopy(checked["effectiveScope"])}
                host.store.proposals[identifier] = entry
                results.append({"candidateId": identifier, "baseVersion": snapshot["version"],
                                "strategy": item["strategy"], "label": candidate_agent.LABELS[item["strategy"]],
                                "rationale": _candidate_rationale(item),
                                "patch": deepcopy(item["patch"]), "document": checked["draft"],
                                "changedPaths": checked["changedPaths"], "derivedChangedPaths": checked["derivedChangedPaths"], "differences": _differences(checked),
                                "validationReport": entry["validationReport"], "expiresAt": expires_at})
            for result in results:
                checked_item = next(item for item in accepted if item["strategy"] == result["strategy"])
                result["evaluation"] = evaluate(checked_item["checked"], result["patch"], instruction)
                result["a2ui"] = surface(result, effective)
            ordered = sorted(results, key=lambda item: (-item["evaluation"]["score"], item["strategy"]))
            for rank, result in enumerate(ordered, 1):
                result["evaluation"]["rank"] = rank
                host.store.proposals[result["candidateId"]]["evaluation"] = deepcopy(result["evaluation"])
                progress("candidate.validated", {"candidateId": result["candidateId"], "strategy": result["strategy"], "rank": rank})
            metrics = {"generationMs": round((time.perf_counter()-started)*1000,2), "proposed":len(proposals),"accepted":len(results),"qualifiedRate":len(results)/max(1,len(proposals)),"attempts":attempts}
            _LOG.info(json.dumps({"event":"candidate_generation","requestId":request_id,**metrics}))
            while len(host.store.proposals) > 500:
                host.store.proposals.pop(next(iter(host.store.proposals)))
            diagnostics = ["Mock 为确定性规则演示，未调用模型。" if mode == "mock" else "LLM 已通过服务端兼容接口调用；所有候选均由服务端独立审查。",
                           "预览只读；只有点击采用方案后才写入正式行程。",
                           "效率策略不代表已核实真实交通距离或行驶时间。"]
            if len(results) < 3:
                diagnostics.append(f"仅有{len(results)}个合格且有实质差异的候选；受字段范围、时间空间或去重限制，不以非法/重复候选凑数。")
                missing = set(candidate_agent.STRATEGIES) - {item["strategy"] for item in accepted}
                for strategy in candidate_agent.STRATEGIES:
                    if strategy in missing and not any(item["strategy"] == strategy for item in rejected):
                        rejected.append(_failure(strategy, "NO_STRATEGY_PROPOSAL", "该策略在当前授权字段和时间空间内没有产生有效提案。"))
            return {"requestId": request_id, "baseVersion": snapshot["version"], "generationMode": mode,
                    "requestedCount": 3, "candidateCount": len(results), "effectiveScope": effective,
                    "candidates": results, "rejectedCandidates": rejected, "diagnostics": diagnostics, "metrics":metrics,
                    "diversity": [diversity(results[i],results[j]) for i in range(len(results)) for j in range(i+1,len(results))]}

    @app.post("/api/sessions/{session_id}/edits/candidates")
    def candidates(session_id: str, body: CandidatesBody, request: Request):
        if "text/event-stream" in request.headers.get("accept", ""):
            return stream_response(session_id, body, build_candidates)
        return build_candidates(session_id, body)

    @app.post("/api/sessions/{session_id}/edits/commit")
    def commit(session_id: str, body: CandidateCommitBody):
        commit_started = time.perf_counter()
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            entry = host.store.proposals.get(body.candidateId)
            if not entry or entry.get("kind") != "phase2_candidate" or entry.get("sessionId") != session_id:
                raise HTTPException(410, {"code": "CANDIDATE_EXPIRED", "message": "候选不存在、已采用或已失效，请重新生成。"})
            if time.monotonic() >= entry["expiresMonotonic"]:
                host.store.proposals.pop(body.candidateId, None)
                raise HTTPException(410, {"code": "CANDIDATE_EXPIRED", "message": "候选已过期，请基于当前行程重新生成。"})
            host.require_version(session, entry["baseVersion"])
            checked = validate_patch(session, entry["selectedComponentIds"], deepcopy(entry["patch"]), entry["fields"])
            payload = host.payload_of(session)
            payload.update(checked["payload"])
            payload["editorAudit"] = {"candidateId": body.candidateId, "requestId": entry["requestId"],
                "baseVersion": session["version"], "parentVersion": session["version"],
                "userRequest": entry["instruction"], "strategy": entry["strategy"], "generationMode": entry["generationMode"],
                "evaluation": deepcopy(entry.get("evaluation")),
                "selectedComponentIds": deepcopy(entry["selectedComponentIds"]), "patch": deepcopy(entry["patch"]),
                "allowedPaths": checked["effectiveScope"]["allowedPaths"], "changedPaths": checked["changedPaths"], "derivedChangedPaths": checked["derivedChangedPaths"],
                "validationReport": {"valid": True, **checked["validationReport"]},
                "constraintsSnapshot": deepcopy(session["persistedConstraints"])}
            result = host.commit(session, payload, "采用候选：" + candidate_agent.LABELS[entry["strategy"]], "candidate_commit")
            for identifier in list(host.store.proposals):
                if host.store.proposals[identifier].get("sessionId") == session_id:
                    host.store.proposals.pop(identifier, None)
            _LOG.info(json.dumps({"event":"candidate_commit","candidateId":body.candidateId,"version":result["version"],"commitMs":round((time.perf_counter()-commit_started)*1000,2)}))
            return result
