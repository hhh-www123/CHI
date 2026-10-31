"""Phase 3 explicit history restore and trusted presentation/constraint controls."""
from copy import deepcopy
from typing import Literal
from fastapi import HTTPException, Query
from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator
from .editor_api import VersionedEditor
from .trip_document import component_registry
from .versioning import _difference, SNAPSHOT_KEYS

class RestoreBody(VersionedEditor):
    targetVersion: StrictInt = Field(ge=1)
    confirmRestore: StrictBool

    @field_validator("confirmRestore")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value

class DisplayBody(VersionedEditor):
    selectedComponentId: str = Field(min_length=1, max_length=160)
    view: Literal["cards", "timeline"] | None = None
    compact: StrictBool | None = None

class GlobalPresentationBody(VersionedEditor):
    confirmGlobal: StrictBool

    @field_validator("confirmGlobal")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value
    showBudgetDetails: StrictBool

class ConstraintsBody(VersionedEditor):
    confirmConstraintChange: StrictBool

    @field_validator("confirmConstraintChange")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value
    maxBudget: StrictInt | StrictFloat | None = Field(ge=100, le=100000)




def install_history_api(app, host):
    def read(session_id, version=None):
        session = host.normalize_session(host.store.get(session_id))
        if version is not None:
            host.require_version(session, version)
        return session

    def audit(action, paths, selected=None, authorization="explicit_user_action"):
        return {"candidateId": None, "userRequest": action, "selectedComponentIds": selected or [],
                "allowedPaths": paths, "authorization": authorization,
                "validationReport": {"valid": True, "source": "trusted_user_action", "invariants": True}}

    @app.get("/api/sessions/{session_id}/versions")
    def versions(session_id: str):
        with host.store.lock:
            session = read(session_id)
            host.store.ensure_checkpoint(session)
            entries = [{key: value for key, value in record.items() if key not in {"documentSnapshot", "payloadSnapshot"}}
                       for record in host.store.versions(session_id)]
            return {"tripId": session_id, "currentVersion": session["version"], "versions": entries}

    @app.get("/api/sessions/{session_id}/versions/{version}/diff")
    def difference(session_id: str, version: int, againstVersion: int = Query(ge=1)):
        with host.store.lock:
            session = read(session_id)
            host.store.ensure_checkpoint(session)
            before = host.store.version(session_id, againstVersion)
            after = host.store.version(session_id, version)
            changes = _difference(before["payloadSnapshot"], after["payloadSnapshot"])
            return {"tripId": session_id, "fromVersion": againstVersion, "toVersion": version,
                    "changedPaths": [item["path"] for item in changes], "differences": changes}

    @app.get("/api/sessions/{session_id}/versions/{version}")
    def version_detail(session_id: str, version: int):
        with host.store.lock:
            session = read(session_id)
            host.store.ensure_checkpoint(session)
            return host.store.version(session_id, version)

    @app.post("/api/sessions/{session_id}/versions/restore")
    def restore(session_id: str, body: RestoreBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            host.store.ensure_checkpoint(session)
            target = host.store.version(session_id, body.targetVersion)
            snapshot = deepcopy(target["payloadSnapshot"])
            action = f"明确恢复版本 v{body.targetVersion} 的数据、界面、锁定与硬约束"
            record_audit = audit(action, ["/" + key for key in SNAPSHOT_KEYS], authorization="explicit_restore_all_state_and_constraints")
            return host.commit(session, snapshot, action, "history_restore", operation_audit=record_audit, restore_from=body.targetVersion)

    @app.post("/api/sessions/{session_id}/editor/display")
    def display(session_id: str, body: DisplayBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            binding = component_registry(session).get(body.selectedComponentId)
            if not binding or binding["kind"] != "DayCard":
                raise HTTPException(403, {"code": "SCOPE_DENIED", "message": "卡片样式属于整天共享展示，请明确选择目标整天；时段或单卡选区不授予整天修改权限。"})
            if body.view is None and body.compact is None:
                raise HTTPException(422, {"code": "INVALID_REQUEST", "message": "请选择卡片/时间轴或信息密度。"})
            host.require_unlocked(session, binding["regionId"])
            payload = host.payload_of(session)
            region = payload["uiSpec"]["regions"][binding["regionId"]]
            paths = []
            for field in ("view", "compact"):
                value = getattr(body, field)
                if value is not None:
                    region[field] = value
                    paths.append("/uiSpec/regions/" + binding["regionId"] + "/" + field)
            action = "修改" + binding["label"] + "的共享卡片展示"
            return host.commit(session, payload, action, "local_display", operation_audit=audit(action, paths, [body.selectedComponentId]))

    @app.post("/api/sessions/{session_id}/editor/global-presentation")
    def global_presentation(session_id: str, body: GlobalPresentationBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            payload = host.payload_of(session)
            payload["presentationSettings"]["showBudgetDetails"] = body.showBudgetDetails
            action = "明确全局授权：" + ("显示" if body.showBudgetDetails else "收起") + "预算明细（不改预算业务）"
            return host.commit(session, payload, action, "global_presentation", operation_audit=audit(action, ["/presentationSettings/showBudgetDetails"], authorization="explicit_global_presentation_confirmation"))

    @app.post("/api/sessions/{session_id}/editor/constraints")
    def constraints(session_id: str, body: ConstraintsBody):
        with host.store.lock:
            session = read(session_id, body.baseVersion)
            if body.maxBudget is not None and body.maxBudget < session["tripState"]["budgetLimit"]:
                raise HTTPException(400, {"code": "INVARIANT_VIOLATION", "message": "硬预算上限不能低于当前行程预算；请先调整行程预算或提高上限。"})
            payload = host.payload_of(session)
            hard = payload["persistedConstraints"]["hardConstraints"]
            hard[:] = [item for item in hard if item.get("kind") != "max_budget"]
            if body.maxBudget is not None:
                hard.append({"id": "max-budget", "kind": "max_budget", "active": True, "value": body.maxBudget})
            action = "明确修改预算硬上限：" + (str(body.maxBudget) if body.maxBudget is not None else "解除预算硬上限")
            return host.commit(session, payload, action, "constraint_change", operation_audit=audit(action, ["/persistedConstraints/hardConstraints"], authorization="explicit_constraint_change_confirmation"))
