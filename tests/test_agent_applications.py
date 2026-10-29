import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services import agent_applications as agent,discovery_store as store

class SubmissionTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        settings=SimpleNamespace(discovery_db_path=str(Path(self.temp.name)/'agent.sqlite3'),supabase_resume_bucket='resumes')
        self.patch=patch.object(store,'get_settings',return_value=settings);self.patch.start()
        self.config_patch=patch('core.config.get_settings',return_value=settings);self.config_patch.start()
        self.resume_id=str(uuid4());self.application_id=str(uuid4());self.text='Registered nurse with patient care experience.'
        self.job={'source':'local_mock','url':'http://127.0.0.1:8769/apply','company':'Mock Hospital','title':'Registered Nurse','description':'Provide patient care and maintain accurate clinical records. Work collaboratively with the clinical team.','id':'mock-1','resume_id':self.resume_id,'resume_hash':hashlib.sha256(self.text.encode()).hexdigest(),
                  'assessment':{'method':'profession_independent_requirement_coverage','score':90,'mandatory_blockers':[],'eligible_for_automation':True}}
        # Use HTTPS canonical source URL for the domain-state tests; browser adapter has its own local fixture test.
        self.job['url']='https://example.com/nurse'
        self.prefs={'matching_threshold':80,'automation_level':'existing_resume'}
        self.find=patch.object(agent,'find_recommendation',return_value=(self.job,self.prefs));self.find.start()
        self.client=Mock();self.resume_query=Mock();self.app_query=Mock()
        for query in [self.resume_query,self.app_query]:
            for method in ['select','eq','update']:getattr(query,method).return_value=query
        self.resume_query.execute.return_value=SimpleNamespace(data=[{'id':self.resume_id,'file_name':'resume.txt','file_path':'owner/resume','mime_type':'text/plain','resume_text':self.text}])
        self.app_query.execute.return_value=SimpleNamespace(data=[])
        self.client.table.side_effect=lambda name:self.resume_query if name=='resumes' else self.app_query
        self.client.rpc.return_value.execute.return_value=SimpleNamespace(data={'id':self.application_id,'status':'found'})
        self.client.storage.from_.return_value.download.return_value=self.text.encode()
        self.adapter=Mock();self.adapter.supports.return_value=True;self.adapter.submit.return_value={'state':'submitted','reference':'mock-receipt'}
    def tearDown(self):
        self.find.stop();self.patch.stop();self.config_patch.stop();self.temp.cleanup()
    def test_submission_is_tracked_found_then_receipt_journaled_once(self):
        result=agent.submit('owner','run',self.job['url'],self.client,{'name':'Test','email':'test@example.test'},adapter=self.adapter)
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['application_id'],self.application_id)
        payload=self.client.rpc.call_args.args[1]['p_payload']
        self.assertEqual(payload['initial_status'],'found');self.assertEqual(payload['selected_resume_id'],self.resume_id)
        again=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertTrue(again['already_recorded']);self.adapter.submit.assert_called_once()
        self.assertEqual(agent.attempts('other-owner'),[])
        self.app_query.update.assert_called_once()
        self.assertNotIn('status',self.app_query.update.call_args.args[0])
    def test_mandatory_gap_blocks_even_at_high_score(self):
        self.job['assessment']['mandatory_blockers']=['License not established']
        with self.assertRaises(HTTPException):agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.adapter.submit.assert_not_called();self.client.rpc.assert_not_called()
    def test_timeout_is_unknown_and_never_automatically_retried(self):
        self.adapter.submit.side_effect=TimeoutError('SENSITIVE')
        result=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertEqual(result['state'],'outcome_unknown')
        agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.adapter.submit.assert_called_once()
        self.assertNotIn('SENSITIVE',json.dumps(result))
    def test_changed_resume_and_unsupported_adapter_block_before_writes(self):
        with self.assertRaises(HTTPException):agent.submit('owner','run',self.job['url'],self.client,{},adapter=None)
        self.job['resume_hash']='changed'
        with self.assertRaises(HTTPException):agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.client.rpc.assert_not_called()
    def test_found_with_submission_or_receipt_is_never_submitted_again(self):
        for field in ('submitted_at','receipt_confirmed_at'):
            with self.subTest(field=field):
                self.app_query.execute.return_value=SimpleNamespace(data=[{'id':self.application_id,'status':'found','application_url':self.job['url'],field:'2026-10-29T00:00:00Z'}])
                result=agent.submit('owner-'+field,'run',self.job['url'],self.client,{},adapter=self.adapter)
                self.assertEqual(result['state'],'needs_user_action')
        self.adapter.submit.assert_not_called()
    def test_answer_pause_does_not_spend_budget_and_resumes_only_once(self):
        from services.automation import usage
        with store.database() as db:
            agent.setup(db)
            db.execute('insert into agent_attempts(id,owner,job_url,application_id,state,note,created_at,updated_at,run_id) values(?,?,?,?,?,?,?,?,?)',('waiting','owner',self.job['url'],self.application_id,'needs_answers','Awaiting answers',agent.timestamp(),agent.timestamp(),'run'))
        self.assertEqual(usage('owner'),0)
        with patch('services.application_questions.read',return_value={'state':'needs_answers'}):
            result=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
            self.assertEqual(result['state'],'needs_answers');self.adapter.submit.assert_not_called()
        self.app_query.execute.return_value=SimpleNamespace(data=[{'id':self.application_id,'status':'found','application_url':self.job['url'],'job_description':self.job['description']}])
        with patch('services.application_questions.read',return_value={'state':'ready'}):
            result=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertEqual(result['state'],'submitted');self.assertEqual(usage('owner'),1)
        agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.adapter.submit.assert_called_once()

    def test_tracking_query_parameters_do_not_hide_a_previous_submission(self):
        self.app_query.execute.return_value=SimpleNamespace(data=[{'id':self.application_id,'status':'found','application_url':self.job['url']+'?tracking=old','submitted_at':'2026-10-29T00:00:00Z'}])
        result=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertEqual(result['state'],'needs_user_action');self.adapter.submit.assert_not_called()
    def test_explicit_retry_only_for_a_known_pre_submission_block(self):
        from services.browser_applications import FormBlocked
        from services.automation import usage
        self.adapter.submit.side_effect=FormBlocked('LOGIN_REQUIRED','Sign in on the employer site.')
        result=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertEqual(result['state'],'blocked_before_submission');self.assertIn('LOGIN_REQUIRED',result['note']);self.assertEqual(usage('owner'),0)
        with self.assertRaises(HTTPException):agent.retry_blocked('other-owner',self.application_id)
        agent.retry_blocked('owner',self.application_id)
        self.adapter.submit.side_effect=None;self.adapter.submit.return_value={'state':'submitted'}
        self.app_query.execute.return_value=SimpleNamespace(data=[{'id':self.application_id,'status':'found','application_url':self.job['url'],'job_description':self.job['description']}])
        retry=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertEqual(retry['state'],'submitted')
        with self.assertRaises(HTTPException):agent.retry_blocked('owner',self.application_id)
    def test_real_site_opt_in_required_before_any_browser_or_upload(self):
        from services.browser_applications import ApplicationBrowser
        with self.assertRaises(HTTPException) as result:
            agent.submit('owner','run',self.job['url'],self.client,{},adapter=ApplicationBrowser())
        self.assertIn('Real-site submission is turned off',result.exception.detail)
        self.client.storage.from_.return_value.download.assert_not_called();self.client.rpc.assert_not_called()
