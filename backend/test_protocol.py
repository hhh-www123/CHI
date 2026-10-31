"""Protocol and ranking regressions, isolated SQLite and fake model only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from backend import test_candidate_api as fixture
from backend import candidate_agent
from backend.candidate_api import review_candidates
from backend.candidate_ranking import evaluate, WEIGHTS
from backend.protocol_adapter import surface, CATALOG

class ProtocolTests(unittest.TestCase):
    setUp=fixture.CandidateAPITests.setUp
    binding=fixture.CandidateAPITests.binding
    stored=fixture.CandidateAPITests.stored
    candidates=fixture.CandidateAPITests.candidates
    batch=fixture.CandidateAPITests.batch
    generated_proposals=fixture.CandidateAPITests.generated_proposals
    assert_unchanged=fixture.CandidateAPITests.assert_unchanged

    def stream(self, **changes):
        body={"baseVersion":self.session["version"],"selectedComponentIds":[self.pm["componentId"]],"instruction":"安排轻松一些","agentMode":"mock",**changes}
        return self.client.post(self.url+"/edits/candidates",json=body,headers={"Accept":"text/event-stream"})

    def events(self,response):
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_stream_lifecycle_stages_and_no_formal_write(self):
        before=self.stored();response=self.stream();self.assertEqual(response.status_code,200)
        events=self.events(response)
        self.assertEqual(events[0]["type"],"RUN_STARTED");self.assertEqual(events[-1]["type"],"RUN_FINISHED")
        names=[e["name"] for e in events if e["type"]=="CUSTOM"]
        self.assertLess(names.index("generation.started"),names.index("generation.finished"))
        self.assertLess(names.index("validation.finished"),names.index("candidate.validated"))
        batch=next(e["value"] for e in events if e.get("name")=="candidates.complete")
        self.assertEqual(batch["candidateCount"],3)
        self.assertEqual(sorted(c["evaluation"]["rank"] for c in batch["candidates"]),[1,2,3])
        for c in batch["candidates"]:
            self.assertEqual(c["a2ui"][0]["createSurface"]["catalogId"],CATALOG)
            self.assertEqual(c["a2ui"][0]["createSurface"]["surfaceId"],c["candidateId"])
        self.assert_unchanged(before)

    def test_stream_error_no_key_is_terminal_and_safe(self):
        before=self.stored();events=self.events(self.stream(agentMode="llm"))
        self.assertEqual(events[-1]["type"],"RUN_ERROR");self.assertEqual(events[-1]["code"],"503")
        self.assertFalse(any(e.get("name")=="candidates.complete" for e in events));self.assert_unchanged(before)

    def test_malicious_model_patch_never_gets_score_or_surface(self):
        before=self.stored();p=self.generated_proposals()[0];p['patch'][0]['path']='/version'
        with patch.object(candidate_agent,"generate",return_value=[p]):
            events=self.events(self.stream())
        self.assertEqual(events[-1]["type"],"RUN_ERROR")
        self.assertFalse(fixture.module.store.proposals);self.assert_unchanged(before)

    def test_scores_bounded_deterministic_and_explained(self):
        snapshot=self.stored();accepted,_=review_candidates(snapshot,[self.pm['componentId']],None,self.generated_proposals())
        self.assertAlmostEqual(sum(WEIGHTS.values()),1)
        for item in accepted:
            a=evaluate(item['checked'],item['patch'],'安排轻松一些');b=evaluate(item['checked'],item['patch'],'安排轻松一些')
            self.assertEqual(a,b);self.assertTrue(a['evidence']);self.assertTrue(0<=a['score']<=1)
            self.assertTrue(all(0<=x<=1 for x in a['metrics'].values()))

    def test_duplicate_final_state_is_removed_before_ranking(self):
        ps=self.generated_proposals();duplicate=deepcopy(ps[0]);duplicate['strategy']='experience_first'
        accepted,rejected=review_candidates(self.stored(),[self.pm['componentId']],None,[ps[0],duplicate])
        self.assertEqual(len(accepted),1);self.assertEqual(rejected[0]['code'],'SIMILAR_CANDIDATE')

    def test_json_and_stream_commit_same_guard_and_version(self):
        before=self.stored();batch=self.batch();c=batch['candidates'][0]
        response=self.client.post(self.url+'/edits/commit',json={'baseVersion':before['version'],'candidateId':c['candidateId']})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['version'],before['version']+1)
        record=self.client.get(self.url+"/versions/"+str(response.json()["version"])).json()
        self.assertEqual(record["evaluation"],c["evaluation"])
        old=self.client.post(self.url+'/edits/commit',json={'baseVersion':before['version'],'candidateId':c['candidateId']})
        self.assertEqual(old.status_code,409)

    def test_one_retry_on_short_llm_results_does_not_expand_scope(self):
        from dataclasses import replace
        self.settings=replace(self.settings,llm_api_key='synthetic')
        ps=self.generated_proposals()
        with patch.object(candidate_agent,'generate',side_effect=[[ps[0]],ps]) as call:
            batch=self.batch(agentMode='llm')
        self.assertEqual(call.call_count,2);self.assertEqual(batch['candidateCount'],3)
        self.assertEqual(batch['metrics']['attempts'],2)
        self.assertEqual(call.call_args_list[0].args[2],call.call_args_list[1].args[2])
