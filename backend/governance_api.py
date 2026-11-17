"""Versioned, user-confirmed requirements and auditable multi-round reports."""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, StrictBool, StrictInt, model_validator

from .editor_api import VersionedEditor
from .fidelity import compute_fidelity
from .requirements import active_requirements, component_value
from .trip_document import component_registry


class RequirementBody(VersionedEditor):
    action: Literal['add', 'replace', 'retire']
    confirm: StrictBool
    requirementId: str | None = Field(default=None, max_length=100)
    text: str = Field(default='', max_length=500)
    kind: Literal['preference', 'keep_hotel', 'keep_component', 'cost_ceiling'] = 'preference'
    componentId: str | None = Field(default=None, max_length=160)
    maxCost: StrictInt | None = Field(default=None, ge=100, le=1000000)
    sourceInstruction: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def validate_action(self):
        if not self.confirm or (self.action != 'add' and not self.requirementId):
            raise ValueError('需求修改须明确确认并提供目标')
        if self.action != 'retire' and not self.text.strip():
            raise ValueError('需求内容不能为空')
        if self.action != 'retire' and self.kind == 'cost_ceiling' and self.maxCost is None:
            raise ValueError('请提供费用上限')
        return self


class ReviewBody(VersionedEditor):
    targetVersion: StrictInt = Field(ge=1)
    requirementId: str = Field(min_length=1, max_length=100)
    verdict: Literal['satisfied', 'unsatisfied']
    note: str = Field(default='', max_length=500)


def install_governance_api(app, host):
    @app.post('/api/sessions/{session_id}/requirements')
    def update_requirements(session_id: str, body: RequirementBody):
        with host.store.lock:
            session = host.normalize_session(host.store.get(session_id))
            host.require_version(session, body.baseVersion)
            entries = deepcopy(session.get('requirements', []))
            target = next((item for item in entries if item['id'] == body.requirementId), None)
            if body.action != 'add' and (target is None or target['status'] != 'active'):
                raise HTTPException(409, '该需求已变化，请刷新后重新选择。')
            if len(entries) >= 200 and body.action != 'retire':
                raise HTTPException(422, '本行程的需求记录已达到 200 条上限。')
            identifier = 'req-goal-' + uuid4().hex
            next_version = session['version'] + 1
            if target:
                target.update(status='retired' if body.action == 'retire' else 'superseded', endedVersion=next_version)
                if body.action == 'replace':
                    target['supersededBy'] = identifier
            if body.action != 'retire':
                binding = component_registry(session).get(body.componentId)
                if body.componentId and binding is None:
                    raise HTTPException(403, '需求范围不在当前行程组件中。')
                item = {'id': identifier, 'text': body.text.strip(), 'kind': body.kind, 'status': 'active',
                        'introducedVersion': next_version, 'createdAt': datetime.now(timezone.utc).isoformat(),
                        'sourceInstruction': body.sourceInstruction.strip() or body.text.strip(),
                        'scopeLabel': binding['label'] if binding else '全程', 'componentId': body.componentId,
                        'replaces': target['id'] if target else None}
                if body.kind == 'keep_component':
                    if binding is None:
                        raise HTTPException(422, '请选择要保留的日期、时段或景点。')
                    item['expected'] = component_value(session, body.componentId)
                elif body.kind == 'keep_hotel':
                    item['componentId'] = None
                    item['scopeLabel'] = '酒店选择'
                    item['expected'] = session['tripState']['hotelId']
                elif body.kind == 'cost_ceiling':
                    item['componentId'] = None
                    item['scopeLabel'] = '全程费用估算'
                    item['maxCost'] = body.maxCost
                entries.append(item)
            payload = host.payload_of(session)
            payload['requirements'] = entries
            action = {'add': '确认持续需求', 'replace': '替换持续需求', 'retire': '停用持续需求'}[body.action]
            return host.commit(session, payload, action + '：' + (target['text'] if body.action == 'retire' else body.text.strip()),
                               'requirement_change', operation_audit={'userRequest': body.sourceInstruction or body.text or target['text'],
                               'allowedPaths': ['/requirements'], 'authorization': 'explicit_requirement_confirmation'})

    @app.get('/api/sessions/{session_id}/fidelity')
    def fidelity(session_id: str):
        with host.store.lock:
            session = host.normalize_session(host.store.get(session_id))
            host.store.ensure_checkpoint(session)
            records = sorted(host.store.versions(session_id), key=lambda item: item['version'])
            previous_by_version = {item['version']: item for item in records}
            rows = []
            for record in records:
                evidence = deepcopy(record.get('fidelity'))
                if evidence is None:
                    previous = previous_by_version.get(record.get('parentVersion'))
                    before = {'id': session_id, 'version': previous['version'], **previous['payloadSnapshot']} if previous else None
                    after = {'id': session_id, 'version': record['version'], **record['payloadSnapshot']}
                    evidence = compute_fidelity(before, after, record['kind'], record)
                for goal in evidence['requirements']:
                    goal['review'] = next((review for review in reversed(session.get('requirementReviews', []))
                                           if review['targetVersion'] == record['version'] and review['requirementId'] == goal['id']), None)
                rows.append({'version': record['version'], 'baseVersion': record['baseVersion'], 'kind': record['kind'],
                             'summary': record['summary'], 'createdAt': record['createdAt'],
                             'userRequest': record['userRequest'], 'changedPaths': record['payloadChangedPaths'], **evidence})
            scoped = [row for row in rows if row['unauthorizedPaths'] is not None]
            protected = sum(row['protectedLeafCount'] for row in scoped)
            checked = [check for row in rows for check in row['checks']]
            return {'tripId': session_id, 'currentVersion': session['version'], 'rows': rows,
                    'summary': {'versionCount': len(rows), 'verifiedScopedEdits': len(scoped),
                                'unauthorizedPathCount': sum(len(row['unauthorizedPaths']) for row in scoped) if scoped else None,
                                'stateRetentionRate': sum(row['preservedLeafCount'] for row in scoped) / protected if protected else None,
                                'functionalChecksPassed': sum(check['passed'] for check in checked), 'functionalChecksTotal': len(checked)}}

    @app.post('/api/sessions/{session_id}/fidelity/reviews')
    def review_requirement(session_id: str, body: ReviewBody):
        with host.store.lock:
            session = host.normalize_session(host.store.get(session_id))
            host.require_version(session, body.baseVersion)
            host.store.ensure_checkpoint(session)
            record = host.store.version(session_id, body.targetVersion)
            goal = next((item for item in active_requirements(record['payloadSnapshot']) if item['id'] == body.requirementId), None)
            if not goal or goal['kind'] != 'preference':
                raise HTTPException(422, '请选择该版本仍有效的自由文本需求进行人工核验。')
            reviews = deepcopy(session.get('requirementReviews', []))
            if len(reviews) >= 1000:
                raise HTTPException(422, '本行程人工核验记录已达到上限。')
            reviews.append({'requirementId': goal['id'], 'targetVersion': body.targetVersion, 'verdict': body.verdict,
                            'note': body.note.strip(), 'reviewedAt': datetime.now(timezone.utc).isoformat(),
                            'recordedVersion': session['version'] + 1})
            payload = host.payload_of(session)
            payload['requirementReviews'] = reviews
            return host.commit(session, payload, f"人工核验 v{body.targetVersion} 的需求：{goal['text']}", 'requirement_review',
                               operation_audit={'allowedPaths': ['/requirementReviews'], 'authorization': 'explicit_manual_review'})
