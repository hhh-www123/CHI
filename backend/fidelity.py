"""Per-version evidence from immutable snapshots and replayed trusted guards."""
from copy import deepcopy

from fastapi import HTTPException

from .domain import session_catalog
from .patch_guard import validate_invariants, validate_patch
from .requirements import active_requirements, evaluate_requirement
from .trip_document import component_registry, to_trip_document, validate_document_schema, validate_locked_unchanged
from .versioning import _difference, payload_snapshot


def leaf_values(value, path=''):
    if isinstance(value, dict) and value:
        return {key: item for name, child in value.items()
                for key, item in leaf_values(child, path + '/' + name.replace('~', '~0').replace('/', '~1')).items()}
    if isinstance(value, list) and value:
        return {key: item for index, child in enumerate(value)
                for key, item in leaf_values(child, path + '/' + str(index)).items()}
    return {path: value}


def compute_fidelity(before, after, kind, audit):
    checks = []
    if 'tripState' not in after:
        return {'checks': [], 'requirements': [], 'unauthorizedPaths': None,
                'protectedLeafCount': None, 'preservedLeafCount': None, 'stateRetentionRate': None,
                'scopeEvidence': '该历史记录没有行程快照', 'lockedStatePreserved': None, 'identityPreserved': None}
    document = to_trip_document(after)
    for name, check in (
        ('行程结构', lambda: validate_document_schema(document)),
        ('时间顺序与硬约束', lambda: validate_invariants(document, after['persistedConstraints'])),
    ):
        try:
            check()
            checks.append({'name': name, 'passed': True})
        except (HTTPException, ValueError, KeyError):
            checks.append({'name': name, 'passed': False})
    catalog = session_catalog(after)
    place_ids = {item['id'] for item in catalog['places']}
    hotel_ids = {item['id'] for item in catalog['hotels']}
    references_valid = after['tripState']['hotelId'] in hotel_ids and all(
        stop['placeId'] in place_ids for day in after['tripState']['days'].values() for stop in day['stops'])
    checks.append({'name': '地点与酒店引用', 'passed': references_valid})
    result = {'checks': checks, 'requirements': [evaluate_requirement(after, item) for item in active_requirements(after)],
              'unauthorizedPaths': None, 'protectedLeafCount': None, 'preservedLeafCount': None,
              'stateRetentionRate': None, 'scopeEvidence': '此操作未记录受限 Patch 授权，范围指标不适用',
              'lockedStatePreserved': None, 'identityPreserved': None}
    if before is None or kind not in {'candidate_commit', 'restricted_patch'}:
        return result
    try:
        validate_locked_unchanged(before, after)
        result['lockedStatePreserved'] = True
    except HTTPException:
        result['lockedStatePreserved'] = False
    try:
        fields = sorted({path.rsplit('/', 1)[-1] for path in audit.get('allowedPaths', [])})
        replay = validate_patch(before, audit['selectedComponentIds'], audit['patch'], fields)
        expected = payload_snapshot(before)
        expected.update(deepcopy(replay['payload']))
        actual = payload_snapshot(after)
        unauthorized = _difference(expected, actual)
        # Count every protected payload leaf, including hidden business state.
        registry = component_registry(before)
        permitted = set()
        for binding in registry.values():
            if binding['kind'] != 'ActivityCard':
                continue
            stops = before['tripState']['days'][binding['dayKey']]['stops']
            index = next(i for i, stop in enumerate(stops) if stop['id'] == binding['stopId'])
            for field in fields:
                path = '/entities/activities/' + binding['entityId'] + '/' + field
                if path in audit.get('allowedPaths', []):
                    permitted.add(f"/tripState/days/{binding['dayKey']}/stops/{index}/{field}")
        permitted.update(change['path'] for change in _difference(payload_snapshot(before), expected))
        old_leaves, new_leaves = leaf_values(payload_snapshot(before)), leaf_values(actual)
        protected = [path for path in old_leaves.keys() | new_leaves.keys()
                     if not any(path == allowed or path.startswith(allowed + '/') for allowed in permitted)]
        preserved = sum(path in old_leaves and path in new_leaves and old_leaves[path] == new_leaves[path] for path in protected)
        identity = set(component_registry(before)) == set(component_registry(after))
        result.update(unauthorizedPaths=[item['path'] for item in unauthorized],
                      protectedLeafCount=len(protected), preservedLeafCount=preserved,
                      stateRetentionRate=preserved / len(protected) if protected else None,
                      scopeEvidence='按保存的选区与 Patch 重放，核对完整持久快照',
                      identityPreserved=identity)
        checks.append({'name': '稳定组件身份', 'passed': identity})
    except (HTTPException, KeyError, ValueError, TypeError, StopIteration):
        result['scopeEvidence'] = '历史授权或快照不足以完成重放，范围指标未验证'
    return result
