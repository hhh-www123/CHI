"""Phase 1 HTTP adapter: trusted selection, single fixed Patch preview, explicit apply.

Reuses the existing cookie ownership, Store lock/CAS, snapshots and history. No
Agent, candidate generation, protocol events or source-code execution occurs here.
"""
from copy import deepcopy
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from .trip_document import (component_registry, component_is_locked, locked_component_ids,
                            set_component_lock, to_trip_document)
from .patch_guard import resolve_scope, validate_patch


class VersionedEditor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseVersion: StrictInt = Field(ge=1)


class SelectionBody(VersionedEditor):
    selectedComponentId: str = Field(min_length=1, max_length=160)
    fields: list[Literal["description", "startTime", "placeId"]] = Field(min_length=1, max_length=3)


class PreviewBody(SelectionBody):
    # The guard validates the exact op shape; no coercion or ignored extra keys.
    patch: list = Field(min_length=1, max_length=30)


class ComponentLockBody(VersionedEditor):
    componentId: str = Field(min_length=1, max_length=160)
    locked: StrictBool


class EditorApplyBody(VersionedEditor):
    previewId: str = Field(min_length=1, max_length=100)


class PresentationBody(VersionedEditor):
    targetRegionId: str = Field(pattern=r"^day-(?:[1-9]|1[0-4])$")
    view: Literal["cards", "timeline"]


def install_editor_api(app, host):
    def read(session_id, version=None):
        session = host.normalize_session(host.store.get(session_id))
        if version is not None:
            host.require_version(session, version)
        return session

    def effective(session, body):
        scope = resolve_scope(session, [body.selectedComponentId], body.fields)
        registry = component_registry(session)
        locked = component_is_locked(body.selectedComponentId, registry,
                                     locked_component_ids(session, registry))
        return {**scope, "selectedComponentId": body.selectedComponentId,
                "label": " / ".join(scope.get("labels", [])), "locked": locked,
                "blockedReason": "此组件或其父区域已锁定。" if locked else
                    ("当前选区没有未锁定的活动，请选择有活动的日期、时段或卡片。" if not scope["allowedPaths"] else ""),
                "version": session["version"]}

    @app.get("/api/sessions/{session_id}/document")
    def document(session_id: str):
        with host.store.lock:
            session = read(session_id)
            return {"document": to_trip_document(session),
                    "constraints": deepcopy(session["persistedConstraints"]),
                    "registry": component_registry(session), "version": session["version"]}

    @app.post("/api/sessions/{session_id}/editor/scope")
    def scope(session_id: str, body: SelectionBody):
        with host.store.lock:
            return effective(read(session_id, body.baseVersion), body)

    @app.patch("/api/sessions/{session_id}/editor/locks")
    def locks(session_id: str, body: ComponentLockBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            change = set_component_lock(session, body.componentId, body.locked)
            candidate = host.payload_of(session)
            candidate.update(change)
            result = host.commit(session, candidate,
                                 ("锁定" if body.locked else "解锁") + component_registry(session)[body.componentId]["label"],
                                 "component_lock")
            return result

    @app.post("/api/sessions/{session_id}/editor/preview")
    def preview(session_id: str, body: PreviewBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            checked = validate_patch(session, [body.selectedComponentId], body.patch, body.fields)
            identifier = "p1-" + uuid4().hex
            # Only an isolated in-memory preview is saved; no Store.save / commit.
            host.store.proposals[identifier] = {
                "kind": "phase1_patch", "sessionId": session_id, "baseVersion": session["version"],
                "selectedComponentId": body.selectedComponentId,
                "fields": deepcopy(body.fields), "patch": deepcopy(body.patch),
            }
            while len(host.store.proposals) > 500:
                host.store.proposals.pop(next(iter(host.store.proposals)))
            return {"previewId": identifier, "baseVersion": session["version"],
                    "document": checked["draft"], "changedPaths": checked["changedPaths"], "derivedChangedPaths": checked["derivedChangedPaths"],
                    "effectiveScope": effective(session, body), "generationMode": "mock",
                    "validationReport": checked["validationReport"],
                    "diagnostics": ["手工固定 Patch 验证演示；未调用 LLM、A2UI 或 AG-UI。"]}

    @app.post("/api/sessions/{session_id}/editor/apply")
    def apply(session_id: str, body: EditorApplyBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            entry = host.store.proposals.get(body.previewId)
            if not entry or entry.get("kind") != "phase1_patch" or entry["sessionId"] != session_id:
                raise HTTPException(404, {"code": "CANDIDATE_EXPIRED", "message": "固定 Patch 预览已失效，请重新验证。"})
            host.require_version(session, entry["baseVersion"])
            # Never accept a client-supplied Patch at apply time. Re-resolve from
            # current registry/constraints and replay the server-held operation.
            checked = validate_patch(session, [entry["selectedComponentId"]], entry["patch"], entry["fields"])
            candidate = host.payload_of(session)
            candidate.update(checked["payload"])
            candidate["editorAudit"] = {
                "baseVersion": session["version"], "selectedComponentIds": [entry["selectedComponentId"]],
                "patch": deepcopy(entry["patch"]), "allowedPaths": checked["effectiveScope"]["allowedPaths"],
                "changedPaths": checked["changedPaths"], "derivedChangedPaths": checked["derivedChangedPaths"], "validationReport": {"valid": True, **checked["validationReport"]},
                "constraintsSnapshot": deepcopy(session["persistedConstraints"]),
            }
            result = host.commit(session, candidate, "应用所选范围的受限 Patch", "restricted_patch")
            for identifier in list(host.store.proposals):
                if host.store.proposals[identifier].get("sessionId") == session_id:
                    host.store.proposals.pop(identifier, None)
            return result

    @app.post("/api/sessions/{session_id}/editor/presentation")
    def presentation(session_id: str, body: PresentationBody):
        # Existing explicit view buttons remain usable without an LLM or code.
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            host.require_unlocked(session, body.targetRegionId)
            candidate = host.payload_of(session)
            candidate["uiSpec"]["regions"][body.targetRegionId]["view"] = body.view
            return host.commit(session, candidate, "切换可信展示组件：" + body.view, "presentation", operation_audit={
                "userRequest": "切换可信展示组件：" + body.view, "candidateId": None,
                "allowedPaths": ["/uiSpec/regions/" + body.targetRegionId + "/view"],
                "selectedComponentIds": [item["componentId"] for item in component_registry(session).values() if item["kind"] == "DayCard" and item["regionId"] == body.targetRegionId],
                "validationReport": {"valid": True, "source": "trusted_presentation_enum", "invariants": True, "locks": True}})
