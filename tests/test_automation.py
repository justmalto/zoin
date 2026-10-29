"""Scheduling, daily budget, draft ownership and approval invariants."""
import hashlib,json,sys,tempfile,unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from fastapi import HTTPException
from services import discovery_store as store,automation,agent_applications as agent
from services.discovery_vault import seal
from test_agent_applications import SubmissionTrackingTests

class DailyLimitTests(SubmissionTrackingTests):
    def test_limit_blocks_before_browser_or_application_writes(self):
        self.prefs['daily_application_limit']=1
        with store.database() as db:
            agent.setup(db)
            db.execute('insert into agent_attempts(id,owner,job_url,state,note,created_at,updated_at) values(?,?,?,?,?,?,?)',('prior','owner','https://example.com/prior','outcome_unknown','unknown',agent.timestamp(),agent.timestamp()))
        with self.assertRaises(HTTPException) as result:agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.assertIn('Daily application limit',result.exception.detail);self.adapter.submit.assert_not_called();self.client.rpc.assert_not_called()
    def test_pause_blocks_submission(self):
        self.prefs['paused']=True
        with self.assertRaises(HTTPException):agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter)
        self.adapter.submit.assert_not_called()
    def test_tailored_exact_bytes_are_uploaded_after_reassessment(self):
        self.prefs['automation_level']='review_resume'
        content={'text':'Tailored source-backed résumé','assessment':self.job['assessment'],'source_hash':self.job['resume_hash'],'file_name':'tailored.docx'}
        result=agent.submit('owner','run',self.job['url'],self.client,{},adapter=self.adapter,tailored=content)
        self.assertEqual(result['state'],'submitted')
        uploaded=self.adapter.submit.call_args.args[2]
        self.assertEqual(uploaded['name'],'tailored.docx');self.assertTrue(uploaded['bytes'].startswith(b'PK'))
        self.client.storage.from_.return_value.download.assert_not_called()
    def test_unapproved_draft_cannot_be_automatically_submitted(self):
        self.prefs['automation_level']='review_resume'
        with store.database() as db:
            automation.setup(db)
            db.execute('insert into agent_drafts values(?,?,?,?,?,?,?,?,?)',('draft','owner','run',self.job['url'],None,'awaiting_approval',seal({'text':'private','assessment':self.job['assessment']}),agent.timestamp(),agent.timestamp()))
        with self.assertRaises(HTTPException):automation.submit_draft('owner','draft',self.client,{},self.adapter)
        with self.assertRaises(HTTPException):automation.draft('other','draft')
        self.adapter.submit.assert_not_called()
    def test_discard_removes_draft_content_but_keeps_tombstone(self):
        with store.database() as db:
            automation.setup(db)
            db.execute('insert into agent_drafts values(?,?,?,?,?,?,?,?,?)',('draft','owner','run',self.job['url'],None,'awaiting_approval',seal({'text':'PRIVATE CONTENT'}),agent.timestamp(),agent.timestamp()))
        automation.discard('owner','draft')
        self.assertEqual(automation.public_draft(automation.draft('owner','draft'),True)['text'],'')

class FrequencyTests(unittest.TestCase):
    def test_frequency_waits_and_manual_or_paused_never_runs(self):
        with tempfile.TemporaryDirectory() as d,patch.object(store,'get_settings',return_value=SimpleNamespace(discovery_db_path=str(Path(d)/'db'))):
            prefs={'daily_enabled':True,'frequency_days':7}
            snap=store.configure('owner',[],prefs,[]);store.enqueue('owner',snap)
            current=store.now()
            with patch.object(store,'now',return_value=current+timedelta(days=6)):self.assertFalse(store.schedule_due('owner',snap))
            with patch.object(store,'now',return_value=current+timedelta(days=7,minutes=1)):self.assertTrue(store.schedule_due('owner',snap))
            self.assertFalse(store.schedule_due('owner',snap|{'preferences':prefs|{'frequency_days':None}}))
            self.assertFalse(store.schedule_due('owner',snap|{'preferences':prefs|{'paused':True}}))
    def test_preferences_do_not_replace_existing_results(self):
        with tempfile.TemporaryDirectory() as d,patch.object(store,'get_settings',return_value=SimpleNamespace(discovery_db_path=str(Path(d)/'db'))):
            snap=store.configure('owner',[],{'matching_threshold':80},[]);run=store.enqueue('owner',snap)
            with store.database() as db:db.execute("update discovery_runs set status='complete',result=? where id=?",(json.dumps({'jobs':[{'url':'https://example.com/job','match_score':85}]}),run['id']))
            updated=store.save_preferences('owner',{'matching_threshold':95,'frequency_days':7,'daily_enabled':True})
            self.assertEqual(updated['run']['result']['jobs'][0]['match_score'],85)
            self.assertEqual(updated['preferences']['matching_threshold'],95)

class AutomaticModeTests(SubmissionTrackingTests):
    def test_level_four_runs_without_approval_but_level_three_waits(self):
        self.prefs['automation_level']='tailored_resume'
        current={'preferences':self.prefs,'run':{'id':'run','status':'complete','result':{'jobs':[self.job]}}}
        with patch.object(store,'latest',return_value=current),patch.object(automation,'create_draft',return_value={'id':'draft','state':'ready'}) as create,patch.object(automation,'submit_draft',return_value={'state':'submitted','note':'Recorded'}) as submit:
            automation.process_owner('owner',self.client,{'name':'Test','email':'test@example.test'},self.adapter)
            create.assert_called_once();submit.assert_called_once()
            self.assertNotIn('approve',submit.call_args.kwargs)
            submit.reset_mock();self.prefs['automation_level']='review_resume'
            automation.process_owner('owner',self.client,{},self.adapter)
            submit.assert_not_called()

    def test_level_three_resumes_an_already_approved_draft_after_answers(self):
        self.prefs['automation_level']='review_resume'
        current={'preferences':self.prefs,'run':{'id':'run','status':'complete','result':{'jobs':[self.job]}}}
        with patch.object(store,'latest',return_value=current),patch.object(automation,'create_draft',return_value={'id':'draft','state':'approved_needs_answers'}),patch.object(automation,'submit_draft',return_value={'state':'submitted','note':'Recorded'}) as submit:
            automation.process_owner('owner',self.client,{},self.adapter)
            submit.assert_called_once()

    def test_found_provider_failure_does_not_block_existing_recommendations(self):
        self.prefs['automation_level']='existing_resume'
        current={'preferences':self.prefs,'run':{'id':'run','status':'complete','result':{'jobs':[self.job]}}}
        with patch.object(store,'latest',return_value=current),patch.object(automation,'found_followups',side_effect=RuntimeError('provider')),patch.object(agent,'submit',return_value={'state':'needs_answers','note':'Questions required'}) as submit:
            automation.process_owner('owner',self.client,{},self.adapter)
            submit.assert_called_once()

class AnalyticsTests(unittest.TestCase):
    def test_cohort_excludes_found_and_retains_interview_milestone_after_rejection(self):
        from routers.discovery import application_analytics
        stamp=store.now().isoformat()
        query=Mock()
        for method in ['select','eq','order','range']:getattr(query,method).return_value=query
        rows=[{'id':'a','status':'rejected','created_at':stamp,'submitted_at':stamp,'receipt_confirmed_at':stamp},{'id':'b','status':'found','created_at':stamp,'submitted_at':stamp,'receipt_confirmed_at':None}]
        events=[{'application_id':'a','to_status':'interview','event_type':'status_change','happened_at':stamp},{'application_id':'a','to_status':'applied','event_type':'application_received','happened_at':stamp},{'application_id':'b','to_status':'applied','event_type':'application_received','happened_at':stamp}]
        query.execute.side_effect=[SimpleNamespace(data=rows),SimpleNamespace(data=events)]
        client=Mock();client.table.return_value=query
        with patch('routers.discovery.user_client',return_value=client),patch('core.config.get_settings',return_value=SimpleNamespace(discovery_enabled=False)):
            result=application_analytics(SimpleNamespace(state=SimpleNamespace(access_token='test')),'owner','30')
        self.assertEqual((result['applied'],result['interview'],result['rejected']),(1,1,1))
        self.assertEqual(sum(result['weekly_submissions']),1)
