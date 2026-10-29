import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services import job_discovery as discovery, discovery_store as store
from schemas.discovery import DiscoveryPreferences

RESUMES = [
    {'id':'sde-resume','file_name':'SDE.pdf','resume_text':'Software Engineer. Java, React, TypeScript, SQL, PostgreSQL, Docker, REST API, algorithms.'},
    {'id':'ml-resume','file_name':'AI.pdf','resume_text':'Data Scientist. Python, SQL, pandas, machine learning, PyTorch, feature engineering, scikit-learn.'},
]
PREFS = DiscoveryPreferences().model_dump()

def job(track, n=1):
    return {'id':str(n),'url':f'https://example.com/{track}/{n}', 'title':'Software Engineer' if track=='sde' else 'Machine Learning Engineer', 'company':'Example', 'location':'Bengaluru, India',
            'description': ('Build products with Java, React, TypeScript, SQL, Docker and REST API. Own well-tested software.' if track=='sde' else 'Build machine learning models with Python, pandas, PyTorch, SQL and feature engineering. Evaluate models.'), 'source':'fixture','board':'example'}


def fixture_profiles():
    return [{'id':r['id'],'name':r['file_name'],'hash':'hash-'+r['id'],
             'directions':[{'name':name,'role_titles':roles,'evidence_ids':['R1']}],
             'tracks':[name.casefold()],'skills':skills}
            for r,name,roles,skills in [(RESUMES[0],'Software engineering',['Software Engineer'],['java','react','typescript','sql','docker','rest api']),
                                       (RESUMES[1],'Data science',['Data Scientist','Machine Learning Engineer'],['python','sql','pandas','pytorch','machine learning','feature engineering'])]]


class DiscoveryTests(unittest.TestCase):
    def test_independent_profiles_queries_and_no_resume_contact_data(self):
        resumes = [r | {'resume_text': r['resume_text']+' email private@example.com Phone +1234567'} for r in RESUMES]
        from schemas.career_agent import CareerProfile
        def caller(prompt,payload,model,**kwargs):
            data=json.loads(payload)['resume_passages']
            name='Software engineering' if 'Software Engineer' in ' '.join(data.values()) else 'Data science'
            return CareerProfile(directions=[{'name':name,'role_titles':[name],'evidence_ids':list(data)[:1]}],skills=[{'name':'SQL','evidence_ids':list(data)[:1]}])
        profiles=discovery.profile_resumes(resumes,caller=caller)
        self.assertEqual(profiles[0]['tracks'], ['software engineering'])
        self.assertEqual(profiles[1]['tracks'], ['data science'])
        self.assertNotIn('private@example.com', json.dumps(profiles))
        plans = discovery.search_plan(profiles, PREFS)
        self.assertEqual({p['track'] for p in plans}, {'software engineering','data science'})

    def test_best_resume_and_fair_four(self):
        profiles = fixture_profiles()
        ranked = [discovery.rank_job(job(t,n), profiles, PREFS) for t in ['sde','ai_ml'] for n in range(4)]
        self.assertEqual(ranked[0]['resume_id'], 'sde-resume')
        self.assertEqual(ranked[-1]['resume_id'], 'ml-resume')
        selected = discovery.balanced_top_four(ranked, discovery.search_plan(profiles,PREFS))
        self.assertEqual([j['track'] for j in selected], ['software engineering','data science','software engineering','data science'])
        self.assertEqual(len({j['url'] for j in selected}),4)

    def test_location_remote_eligibility_and_experience_filter(self):
        self.assertFalse(discovery.location_allowed('Remote - United States', PREFS))
        self.assertTrue(discovery.location_allowed('Global - Remote', PREFS))
        self.assertTrue(discovery.location_allowed('Bengaluru, India', PREFS))
        self.assertTrue(discovery.location_allowed('Bangalore', PREFS))
        profiles = fixture_profiles()
        self.assertIsNone(discovery.rank_job(job('sde')|{'title':'Principal Software Engineer'}, profiles, PREFS))
        self.assertIsNotNone(discovery.rank_job(job('sde')|{'title':'Senior Software Engineer'}, profiles, PREFS|{'include_senior_roles':True}))
        j=job('sde');j['description']+=' Requires 8+ years experience.'
        self.assertIsNone(discovery.rank_job(j,profiles,PREFS|{'max_required_years':3}))
        self.assertIsNone(discovery.rank_job(j|{'description':'No skill requirements published'},profiles,PREFS))

    def test_pipeline_dedup_exclusion_and_partial_outage(self):
        profiles=fixture_profiles()
        def fetch(source):
            if source[1]=='sambatv':raise ValueError('Unavailable')
            return [job('sde',1),job('sde',2),job('ai_ml',1)]
        result=discovery.discover(profiles,PREFS,[job('sde',1)['url']+'?tracking=abc'],source_fetcher=fetch,detail_fetcher=lambda j:j,resumes=RESUMES,assessor=lambda *args:{'score':90,'threshold_met':True,'mandatory_blockers':[],'eligible_for_automation':True})
        self.assertEqual(len(result['jobs']),2)
        self.assertTrue(result['warnings'])
        self.assertEqual(result['steps'][-1]['stage'],'balanced_shortlist')

    def test_shortlist_never_pads_and_empty_profiles_do_not_fetch(self):
        result=discovery.discover([],PREFS,source_fetcher=Mock(side_effect=AssertionError('should not fetch')))
        self.assertEqual(result['jobs'],[])
        self.assertTrue(result['warnings'])

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'local.sqlite3'
        self.settings=patch.object(store,'get_settings',return_value=SimpleNamespace(discovery_db_path=str(self.path)))
        self.settings.start()
    def tearDown(self):
        self.settings.stop();self.directory.cleanup()
    def test_queue_persists_daily_dedup_owner_isolation_and_delete(self):
        snapshot=store.configure('owner-a',fixture_profiles(),PREFS,[])
        first=store.enqueue('owner-a',snapshot)
        self.assertEqual(first['id'],store.enqueue('owner-a',snapshot)['id'])
        self.assertIsNone(store.latest('owner-b')['run'])
        self.assertTrue(store.process_one(runner=lambda *args:{'jobs':[job('sde')]}))
        result=store.latest('owner-a')['run']
        self.assertEqual(result['status'],'complete')
        self.assertNotEqual(first['id'],store.enqueue('owner-a',snapshot|{'excluded':['https://example.com/new']})['id'])
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertNotIn('resume_text',self.path.read_bytes().decode(errors='ignore'))
        store.erase('owner-a');self.assertIsNone(store.latest('owner-a')['run'])
    def test_next_day_excludes_previous_recommendations_and_other_owners(self):
        snapshot=store.configure('owner-a',[],PREFS,[])
        with patch.object(store,'day',return_value='2026-10-28'):
            store.enqueue('owner-a',snapshot)
            store.process_one(runner=lambda *args:{'jobs':[job('sde')]})
        with patch.object(store,'day',return_value='2026-10-29'):
            store.enqueue('owner-a',snapshot)
            runner=Mock(return_value={'jobs':[]})
            store.process_one(runner=runner)
            self.assertIn(job('sde')['url'], runner.call_args.args[2])
            other=store.configure('owner-b',[],PREFS,[])
            store.enqueue('owner-b',other)
            runner=Mock(return_value={'jobs':[]})
            store.process_one(runner=runner)
            self.assertEqual(runner.call_args.args[2],[])

    def test_failures_are_safe_and_retryable(self):
        snapshot=store.configure('owner-a',[],PREFS,[]);first=store.enqueue('owner-a',snapshot)
        def fail(*args):raise ValueError('SECRET_PROVIDER_DATA')
        store.process_one(runner=fail)
        self.assertEqual(store.latest('owner-a')['run']['status'],'failed')
        self.assertNotIn('SECRET',json.dumps(store.latest('owner-a')))
        self.assertNotEqual(first['id'],store.enqueue('owner-a',snapshot)['id'])

class DiscoveryApiTests(unittest.TestCase):
    def test_auth_and_feature_gate(self):
        from main import create_app
        from core.auth import require_user
        from fastapi.testclient import TestClient
        app=create_app();client=TestClient(app)
        self.assertEqual(client.get('/api/v1/discovery').status_code,401)
        app.dependency_overrides[require_user]=lambda:'owner-a'
        with patch('routers.discovery.get_settings',return_value=SimpleNamespace(discovery_enabled=False)):
            self.assertEqual(client.get('/api/v1/discovery').status_code,404)
        app.dependency_overrides.clear()

class DiscoveryAccountApiTests(unittest.TestCase):
    def test_owner_filters_and_local_queue_use_authenticated_user(self):
        from main import create_app
        from core.auth import require_user
        from fastapi import Request
        from fastapi.testclient import TestClient
        app = create_app()
        def user(request: Request):
            request.state.access_token = 'test-only'
            return 'owner-a'
        app.dependency_overrides[require_user] = user
        client = TestClient(app)
        resumes_query = Mock()
        for method in ['select','eq','limit']:
            getattr(resumes_query,method).return_value = resumes_query
        resumes_query.execute.return_value = SimpleNamespace(data=RESUMES)
        jobs_query = Mock()
        for method in ['select','eq','limit']:
            getattr(jobs_query,method).return_value = jobs_query
        jobs_query.execute.return_value = SimpleNamespace(data=[{'application_url':'https://example.com/applied'}])
        database = Mock();database.table.side_effect = lambda name:resumes_query if name=='resumes' else jobs_query
        with patch('routers.discovery.get_settings', return_value=SimpleNamespace(discovery_enabled=True)), patch('routers.discovery.user_client', return_value=database), patch('routers.discovery.discovery_store.configure', return_value={}) as configure, patch('routers.discovery.discovery_store.enqueue', return_value={'id':'run','status':'queued'}), patch('routers.discovery.discovery_store.process_one'):
            response=client.post('/api/v1/discovery/runs',json=DiscoveryPreferences().model_dump())
            self.assertEqual(response.status_code,202)
            resumes_query.eq.assert_called_with('user_id','owner-a')
            jobs_query.eq.assert_called_with('user_id','owner-a')
            self.assertEqual(configure.call_args.args[0],'owner-a')
            self.assertEqual(configure.call_args.args[3],['https://example.com/applied'])
            self.assertEqual(client.post('/api/v1/discovery/runs',json={'user_id':'owner-b'}).status_code,422)
        app.dependency_overrides.clear()
