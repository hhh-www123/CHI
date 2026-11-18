"""User-confirmed goals, deterministic preservation checks and model context."""
from copy import deepcopy

from fastapi import HTTPException

from .domain import trip_costs, session_catalog
from .trip_document import component_registry, to_trip_document


def component_value(session, component_id):
    registry = component_registry(session)
    binding = registry.get(component_id)
    if not binding:
        return None
    if binding['kind'] == 'DayCard':
        return deepcopy(session['tripState']['days'][binding['dayKey']])
    document = to_trip_document(session)
    if binding['kind'] == 'ActivityCard':
        return deepcopy(document['entities']['activities'].get(binding['entityId']))
    return {item['entityId']: deepcopy(document['entities']['activities'][item['entityId']])
            for item in registry.values()
            if item['kind'] == 'ActivityCard' and item.get('parentId') == component_id}


def evaluate_requirement(session, requirement):
    kind = requirement['kind']
    result = {'id': requirement['id'], 'text': requirement['text'], 'kind': kind,
              'scopeLabel': requirement['scopeLabel'], 'introducedVersion': requirement['introducedVersion']}
    if kind == 'preference':
        return {**result, 'status': 'manual', 'detail': '自由文本需求待人工核验'}
    if kind == 'keep_hotel':
        passed = session['tripState']['hotelId'] == requirement['expected']
        detail = '当前酒店选择与确认时一致' if passed else '酒店选择已发生变化'
    elif kind == 'keep_component':
        passed = component_value(session, requirement['componentId']) == requirement['expected']
        detail = '组件数据与确认快照一致' if passed else '组件数据或所属时段已变化'
    else:
        total = trip_costs(session['tripState'], session_catalog(session))['total']
        passed = total <= requirement['maxCost']
        detail = f"费用估算 ¥{total:g}，目标上限 ¥{requirement['maxCost']:g}"
        result['observedCost'] = total
    return {**result, 'status': 'passed' if passed else 'failed', 'detail': detail}


def active_requirements(session):
    return [item for item in session.get('requirements', []) if item['status'] == 'active']


def validate_requirements(before, after):
    failures = []
    for item in active_requirements(after):
        checked = evaluate_requirement(after, item)
        if checked['status'] != 'failed':
            continue
        # An unmet cost goal can be approached in several scoped edits.
        if item['kind'] == 'cost_ceiling':
            previous = evaluate_requirement(before, item)
            if previous['status'] == 'failed' and checked['observedCost'] <= previous['observedCost']:
                continue
        failures.append(item['text'])
    if failures:
        raise HTTPException(422, {'code': 'REQUIREMENT_CONFLICT',
            'message': '修改与持续需求冲突，请调整方案或在历史需求中明确替换/停用需求。' + '；'.join(failures)})


def model_requirements(session):
    return [{key: deepcopy(value) for key, value in item.items()
             if key in {'id', 'text', 'kind', 'componentId', 'scopeLabel', 'introducedVersion', 'sourceInstruction', 'maxCost', 'expected'}}
            for item in active_requirements(session)]
