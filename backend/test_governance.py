"""Goal lifecycle, scoped preservation and per-version evidence regressions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

from backend import access, app as module, candidate_api, candidate_agent
from backend.config import Settings
from backend.fidelity import compute_fidelity
from backend.persistence import Store
from backend.patch_guard import resolve_scope
from backend.requirements import model_requirements


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = module.store
        module.store = Store(Path(self.directory.name))
        self.addCleanup(setattr, module, 'store', previous)
        settings = Settings(agent_mode='demo', candidate_agent_mode='mock', requests_per_minute=1000)
        for owner in (module, access, candidate_api, candidate_agent):
            handle = patch.object(owner, 'get_settings', return_value=settings)
            handle.start()
            self.addCleanup(handle.stop)
        with access._bucket_lock:
            access._buckets.clear()
        self.client = TestClient(module.app)
        self.addCleanup(self.client.close)
        self.session = self.client.post('/api/sessions', json={}).json()
        self.url = '/api/sessions/' + self.session['id']

    def update_goal(self, **change):
        response = self.client.post(self.url + '/requirements', json={
            'baseVersion': self.session['version'], 'confirm': True,
            'action': 'add', 'kind': 'preference', 'text': '每天留出休息时间', **change})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        return self.session['requirements'][-1]

    def binding(self, day='day3', kind='DayCard'):
        return next(item for item in self.session['componentRegistry'].values()
                    if item['dayKey'] == day and item['kind'] == kind)

    def candidate(self):
        response = self.client.post(self.url + '/edits/candidates', json={
            'baseVersion': self.session['version'], 'selectedComponentIds': [self.binding()['componentId']],
            'instruction': '安排轻松一些', 'agentMode': 'mock', 'fields': ['description', 'startTime']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['candidates'][0]

    def apply_candidate(self):
        candidate = self.candidate()
        response = self.client.post(self.url + '/edits/commit', json={
            'baseVersion': self.session['version'], 'candidateId': candidate['candidateId']})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()

    def test_legacy_session_and_goal_lifecycle(self):
        stored = module.store.get(self.session['id'])
        stored.pop('requirements', None)
        self.assertEqual(module.normalize_session(stored)['requirements'], [])
        first = self.update_goal(text='预算目标3000元', kind='cost_ceiling', maxCost=3000)
        second = self.update_goal(action='replace', requirementId=first['id'], text='预算目标3500元', kind='cost_ceiling', maxCost=3500)
        self.assertEqual(self.session['requirements'][0]['status'], 'superseded')
        self.assertEqual(self.session['requirements'][0]['supersededBy'], second['id'])
        self.assertEqual([item['id'] for item in model_requirements(self.session)], [second['id']])
        self.update_goal(action='retire', requirementId=second['id'])
        self.assertEqual(model_requirements(self.session), [])

    def test_confirmation_version_ownership_and_extra_fields(self):
        before = module.store.get(self.session['id'])
        for change, status in (({'confirm': False}, 422), ({'baseVersion': 999}, 409), ({'expected': 'forged'}, 422)):
            response = self.client.post(self.url + '/requirements', json={'action': 'add', 'confirm': True,
                'baseVersion': self.session['version'], 'text': '保留酒店', **change})
            self.assertEqual(response.status_code, status)
        with TestClient(module.app) as outsider:
            self.assertEqual(outsider.get(self.url + '/fidelity').status_code, 404)
        self.assertEqual(module.store.get(self.session['id']), before)

    def test_hotel_preservation_guards_direct_update(self):
        self.update_goal(kind='keep_hotel', text='保持已确认酒店')
        other = next(item['id'] for item in self.session['catalog']['hotels'] if item['id'] != self.session['tripState']['hotelId'])
        response = self.client.patch(self.url + '/direct', json={'baseVersion': self.session['version'], 'kind': 'hotel', 'value': other})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('REQUIREMENT_CONFLICT', response.text)

    def test_component_requirement_blocks_preview(self):
        self.update_goal(kind='keep_component', componentId=self.binding()['componentId'], text='第三天保持')
        response = self.client.post(self.url + '/edits/candidates', json={
            'baseVersion': self.session['version'], 'selectedComponentIds': [self.binding()['componentId']],
            'instruction': '安排轻松一些', 'agentMode': 'mock'})
        self.assertEqual(response.status_code, 422, response.text)

    def test_requirement_change_invalidates_cached_candidate(self):
        candidate = self.candidate()
        self.update_goal(text='后续轻松一些')
        response = self.client.post(self.url + '/edits/commit', json={'baseVersion': candidate['baseVersion'], 'candidateId': candidate['candidateId']})
        self.assertEqual(response.status_code, 409)

    def test_advertised_place_replacement_phrase(self):
        response = self.client.post(self.url + '/edits/candidates', json={
            'baseVersion': self.session['version'], 'selectedComponentIds': [self.binding()['componentId']],
            'instruction': '换一个景点，保留时间和其他内容', 'agentMode': 'mock',
            'fields': ['description', 'startTime', 'placeId']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['candidateCount'], 3)
        self.assertTrue(all(any(operation['path'].endswith('/placeId') for operation in candidate['patch'])
                            for candidate in response.json()['candidates']))

    def test_model_input_contains_only_active_goals_and_scoped_history(self):
        first = self.update_goal(text='旧偏好')
        latest = self.update_goal(action='replace', requirementId=first['id'], text='新偏好')
        snapshot = module.store.get(self.session['id'])
        snapshot['_requirementHistory'] = [{'version': snapshot['version'], 'request': '新偏好', 'kind': 'requirement_change'}]
        scope = resolve_scope(snapshot, [self.binding()['componentId']])
        proposals = candidate_agent.mock_generate(snapshot, '安排轻松一些', scope)
        response = httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps({'candidates': proposals})}}]})
        with patch.object(candidate_agent.httpx, 'Client') as client:
            transport = client.return_value.__enter__.return_value
            transport.post.return_value = response
            settings = Settings(llm_api_key='local-fixture-only', llm_model='fixture-model')
            candidate_agent.llm_generate(snapshot, '安排轻松一些', scope, settings)
            payload = transport.post.call_args.kwargs['json']
        context = json.loads(payload['messages'][1]['content'])
        self.assertEqual([item['id'] for item in context['activeRequirements']], [latest['id']])
        self.assertEqual(context['recentHistory'], snapshot['_requirementHistory'])
        self.assertEqual(context['effectiveScope']['allowedPaths'], scope['allowedPaths'])

    def test_cost_goal_allows_progress_and_rejects_regression(self):
        self.update_goal(kind='cost_ceiling', text='费用估算控制在100元', maxCost=100)
        current_total = self.session['derived']['total']
        hotels = self.session['catalog']['hotels']
        expensive = max(hotels, key=lambda item: item['nightlyPrice'])
        response = self.client.patch(self.url + '/direct', json={'baseVersion': self.session['version'],
            'kind': 'hotel', 'value': expensive['id']})
        self.assertEqual(response.status_code, 422, response.text)
        economical = min(hotels, key=lambda item: item['nightlyPrice'])
        response = self.client.patch(self.url + '/direct', json={'baseVersion': self.session['version'],
            'kind': 'hotel', 'value': economical['id']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLessEqual(response.json()['derived']['total'], current_total)
        report = self.client.get(self.url + '/fidelity').json()
        self.assertEqual(report['rows'][-1]['requirements'][0]['status'], 'failed')
        response = self.client.post(self.url + '/versions/restore', json={'baseVersion': response.json()['version'],
            'targetVersion': self.session['version'], 'confirmRestore': True})
        self.assertEqual(response.status_code, 200, response.text)

    def test_ten_round_scoped_evidence_and_preserved_hotel(self):
        self.update_goal(kind='keep_hotel', text='保留酒店')
        initial = deepcopy(self.session['tripState'])
        for _ in range(10):
            self.apply_candidate()
        report = self.client.get(self.url + '/fidelity').json()
        self.assertEqual(report['summary']['verifiedScopedEdits'], 10)
        self.assertEqual(report['summary']['unauthorizedPathCount'], 0)
        self.assertEqual(report['summary']['stateRetentionRate'], 1)
        self.assertEqual(self.session['tripState']['days']['day1'], initial['days']['day1'])
        self.assertEqual(self.session['tripState']['hotelId'], initial['hotelId'])
        self.assertTrue(all(check['passed'] for row in report['rows'] for check in row['checks']))
        reopened = Store(Path(self.directory.name))
        self.assertEqual(reopened.get(self.session['id'])['requirements'], self.session['requirements'])
        self.assertEqual(len(reopened.versions(self.session['id'])), report['summary']['versionCount'])

    def test_manual_reviews_are_version_bound_and_persistent(self):
        goal = self.update_goal(text='每天休息一次')
        target = self.session['version']
        self.apply_candidate()
        response = self.client.post(self.url + '/fidelity/reviews', json={'baseVersion': self.session['version'],
            'targetVersion': target, 'requirementId': goal['id'], 'verdict': 'unsatisfied', 'note': '该版本未列休息'})
        self.assertEqual(response.status_code, 200, response.text)
        report = self.client.get(self.url + '/fidelity').json()
        reviewed = next(row for row in report['rows'] if row['version'] == target)
        later = next(row for row in report['rows'] if row['version'] == target + 1)
        self.assertEqual(reviewed['requirements'][0]['review']['verdict'], 'unsatisfied')
        self.assertIsNone(later['requirements'][0]['review'])

    def test_undo_and_restore_include_goal_ledger(self):
        first = self.update_goal(text='旧偏好')
        first_version = self.session['version']
        self.update_goal(action='replace', requirementId=first['id'], text='新偏好')
        response = self.client.post(self.url + '/undo', json={'baseVersion': self.session['version']})
        self.assertEqual(response.status_code, 200, response.text)
        self.session = response.json()
        self.assertEqual(self.session['requirements'][0]['status'], 'active')
        self.assertEqual(len(self.session['requirements']), 1)
        response = self.client.post(self.url + '/versions/restore', json={'baseVersion': self.session['version'],
            'targetVersion': first_version, 'confirmRestore': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['requirements'][0]['id'], first['id'])

    def test_fidelity_detects_hidden_business_change(self):
        before = module.store.get(self.session['id'])
        self.apply_candidate()
        after = module.store.get(self.session['id'])
        record = module.store.version(self.session['id'], self.session['version'])
        after['tripState']['days']['day1']['note'] = 'unauthorized change'
        evidence = compute_fidelity(before, after, record['kind'], record)
        self.assertIn('/tripState/days/day1/note', evidence['unauthorizedPaths'])
        self.assertLess(evidence['stateRetentionRate'], 1)


if __name__ == '__main__':
    unittest.main()
