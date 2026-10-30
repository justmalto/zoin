import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import SecretStr

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from core.config import get_settings
from services.ai_budget import Budget, BudgetBlocked, duration
from services.ai_context import ai_scope, identity
from services import groq_client
from services.resume_tailoring import FactualVerification
from schemas.career_agent import TailoringProposal


class AIBudgetTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.settings=get_settings().model_copy(update={
            'ai_budget_db_path':str(Path(directory.name)/'ai.sqlite3'),
            'groq_api_key':SecretStr('fixture-not-a-real-key'),
            'groq_tokens_per_minute':1000,'groq_tokens_per_day':1500,
            'groq_owner_tokens_per_day':1200,'groq_background_tokens_per_day':900})
        self.budget=Budget(self.settings)

    def reserve(self,amount=600,owner='a',model='120b',background=False):
        return self.budget.reserve(model,owner,'test',amount,background)

    def test_atomic_reservations_prevent_concurrent_overspend(self):
        def attempt(_):
            try:self.reserve();return 'accepted'
            except BudgetBlocked:return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,range(2)))
        self.assertCountEqual(results,['accepted','blocked'])
        with self.assertRaises(BudgetBlocked):Budget(self.settings).reserve('120b','b','test',600)

    def test_measured_usage_reconciles_estimate_once(self):
        key=self.reserve(800)
        self.budget.finish(key,usage={'prompt_tokens':100,'completion_tokens':50,'total_tokens':150})
        self.budget.finish(key,usage={'total_tokens':0})
        self.reserve(800)
        usage=self.budget.summary('a')['usage'][0]
        self.assertEqual(usage['tokens'],950)
        self.assertEqual(usage['prompt_tokens'],100)
        self.assertEqual(usage['completion_tokens'],50)

    def test_daily_owner_and_shared_limits_have_distinct_scopes(self):
        with patch('services.ai_budget.time.time',return_value=1000):self.reserve(800)
        with patch('services.ai_budget.time.time',return_value=1061):
            with self.assertRaises(BudgetBlocked) as failure:self.reserve(500)
            self.assertEqual(failure.exception.code,'account_daily_tokens')
            self.reserve(500,owner='b')
            with self.assertRaises(BudgetBlocked) as failure:self.reserve(300,owner='c')
            self.assertEqual(failure.exception.code,'daily_tokens')

    def test_background_ceiling_protects_interactive_capacity(self):
        with patch('services.ai_budget.time.time',return_value=1000):self.reserve(600,background=True)
        with patch('services.ai_budget.time.time',return_value=1061):
            with self.assertRaises(BudgetBlocked) as failure:self.reserve(500,background=True)
            self.assertEqual(failure.exception.code,'background_daily_tokens')
            self.reserve(500,background=False)

    def test_daily_budget_is_rolling_and_survives_new_instance(self):
        with patch('services.ai_budget.time.time',return_value=1000):self.reserve(900)
        with patch('services.ai_budget.time.time',return_value=1000+86401):
            Budget(self.settings).reserve('120b','a','test',900)

    def test_requests_and_models_have_independent_limits(self):
        self.reserve(900,model='120b');self.reserve(900,model='20b')
        limited=Budget(self.settings.model_copy(update={'groq_requests_per_minute':1}))
        with self.assertRaises(BudgetBlocked) as failure:limited.reserve('20b','b','test',1)
        self.assertEqual(failure.exception.code,'minute_requests')

    def test_provider_cooldown_is_persistent_and_model_specific(self):
        key=self.reserve(model='20b')
        self.budget.finish(key,status='rejected',retry_after=120)
        with self.assertRaises(BudgetBlocked) as failure:Budget(self.settings).reserve('20b','a','test',1)
        self.assertEqual(failure.exception.code,'provider_cooldown')
        self.reserve(model='120b')

    def test_provider_headers_and_duration_parsing(self):
        self.assertAlmostEqual(duration('2m59.56s'),179.56)
        self.assertEqual(duration('250ms'),.25)
        key=self.reserve()
        self.budget.finish(key,usage={'total_tokens':10},headers={'x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'8s'})
        with self.assertRaises(BudgetBlocked) as failure:self.reserve(1)
        self.assertEqual(failure.exception.code,'provider_cooldown')
        self.assertLessEqual(failure.exception.retry_after,8)

    def test_uncertain_requests_keep_their_reservation(self):
        key=self.reserve(600);self.budget.finish(key,status='uncertain')
        row=self.budget.summary('a')['usage'][0]
        self.assertEqual(row['tokens'],600);self.assertEqual(row['estimated_requests'],1)

    def test_validated_cache_is_private_encrypted_and_model_versioned(self):
        calls=[]
        @groq_client.cached_result('fixture')
        def analyze(text):calls.append(text);return {'result':'private resume phrase'}
        with patch.object(groq_client,'get_settings',return_value=self.settings):
            with ai_scope('a'):
                analyze('input');analyze('input')
            with ai_scope('b'):analyze('input')
            self.assertEqual(len(calls),2)
        with patch.object(groq_client,'get_settings',return_value=self.settings.model_copy(update={'groq_resume_model':'openai/gpt-oss-120b'})):
            with ai_scope('a'):analyze('input')
        self.assertEqual(len(calls),3)
        self.assertNotIn(b'private resume phrase',self.budget.path.read_bytes())
        self.assertEqual(self.budget.path.stat().st_mode&0o777,0o600)

    def test_rejected_service_output_is_not_cached(self):
        calls=[]
        @groq_client.cached_result('fixture')
        def reject(text):calls.append(text);raise groq_client.AIError('Invalid evidence')
        with patch.object(groq_client,'get_settings',return_value=self.settings),ai_scope('a'):
            for _ in range(2):
                with self.assertRaises(groq_client.AIError):reject('text')
        self.assertEqual(len(calls),2)

    def test_encrypted_cache_cannot_be_reassigned_to_another_owner(self):
        self.budget.cached('a','same',{'private':'account a'})
        with self.budget.database() as db:
            db.execute("insert into ai_results select 'b',cache_key,content,expires from ai_results where owner='a'")
        self.assertIsNone(self.budget.cached('b','same'))
        self.assertEqual(self.budget.cached('a','same'),{'private':'account a'})

    def test_duplicate_request_claim_survives_restart_and_expires(self):
        with patch('services.ai_budget.time.time',return_value=1000):
            self.budget.claim_cache('a','same')
            with self.assertRaises(BudgetBlocked):Budget(self.settings).claim_cache('a','same')
        with patch('services.ai_budget.time.time',return_value=1121):self.budget.claim_cache('a','same')

    def test_stale_cache_worker_cannot_release_a_new_lease(self):
        with patch('services.ai_budget.time.time',return_value=1000):old=self.budget.claim_cache('a','same')
        with patch('services.ai_budget.time.time',return_value=1121):
            self.budget.claim_cache('a','same');self.budget.release_cache('a','same',old)
            with self.assertRaises(BudgetBlocked):self.budget.claim_cache('a','same')

    def test_temporary_resume_analysis_is_never_cached(self):
        from services.ai_context import temporary_input
        calls=[]
        @groq_client.cached_result('fixture')
        def analyze(text):calls.append(text);return {'evidence':text}
        with patch.object(groq_client,'get_settings',return_value=self.settings),ai_scope('a'),temporary_input():
            analyze('temporary resume');analyze('temporary resume')
        self.assertEqual(len(calls),2)
        self.assertFalse(self.budget.path.exists())

    def test_resetting_discovery_does_not_reset_token_usage(self):
        self.reserve(900);self.budget.forget('a')
        with self.assertRaises(BudgetBlocked):self.reserve(900)

    def test_authenticated_request_identity_reaches_sync_worker(self):
        from main import create_app
        from core.auth import require_user
        from fastapi import Request,Depends
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        app=create_app()
        def verified(request:Request):
            request.state.verified_user=SimpleNamespace(id='verified-owner');return 'verified-owner'
        app.dependency_overrides[require_user]=verified
        @app.get('/context-check')
        def inspect_scope(user=Depends(require_user)):
            return {'owner':identity()[0]}
        result=TestClient(app).get('/context-check')
        self.assertEqual(result.json()['owner'],'verified-owner')
        self.assertEqual(identity()[0],'_unattributed')

    def test_owner_scope_resets_without_leaking_between_workers(self):
        with ai_scope('a',background=True):
            self.assertEqual(identity(),('a',True))
            with ai_scope('b'):self.assertEqual(identity(),('b',True))
            self.assertEqual(identity(),('a',True))
        self.assertEqual(identity(),('_unattributed',False))

    def test_drafting_uses_20b_and_verification_uses_120b(self):
        settings=self.settings.model_copy(update={'groq_tokens_per_minute':7500,'groq_tokens_per_day':160000,'groq_owner_tokens_per_day':80000})
        def provider(*args,**kwargs):
            model=kwargs['json']['response_format']['json_schema']['name']
            value={'faithful':True} if model=='FactualVerification' else {'edits':[],'unaddressed_gaps':[]}
            return httpx.Response(200,json={'usage':{'prompt_tokens':90,'completion_tokens':10,'total_tokens':100},
                'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]},request=httpx.Request('POST','https://api.groq.com'))
        with patch.object(groq_client,'get_settings',return_value=settings),patch.object(groq_client.httpx,'post',side_effect=provider) as post,ai_scope('a'):
            groq_client.complete_json('system','text',TailoringProposal)
            groq_client.complete_json('system','text',FactualVerification)
        self.assertEqual([c.kwargs['json']['model'] for c in post.call_args_list],['openai/gpt-oss-20b','openai/gpt-oss-120b'])
        self.assertEqual(post.call_args_list[-1].kwargs['json']['max_completion_tokens'],400)
        self.assertEqual(sum(r['tokens'] for r in self.budget.summary('a')['usage']),200)

    def test_usage_endpoint_is_authenticated_and_owner_scoped(self):
        from main import create_app
        from core.auth import require_user
        from fastapi.testclient import TestClient
        app=create_app();client=TestClient(app)
        self.assertEqual(client.get('/api/v1/ai/usage').status_code,401)
        self.reserve(owner='b');app.dependency_overrides[require_user]=lambda:'a'
        with patch('routers.ai_usage.get_settings',return_value=self.settings):
            result=client.get('/api/v1/ai/usage').json()
        self.assertEqual(result['usage'],[])
