import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services.mail_workflow import run_mail_workflow, shortlist
from services.imap_client import fetch_job_messages
from services.groq_client import AIError

APP={'company':'Zoin Synthetic Labs','job_title':'Data Scientist','job_id':'ZOIN-TEST-26'}
GOOD={'uid':10,'message_id':'test1','sender':'test@example.invalid','subject':'ZOIN-TEST-26 interview','body_text':'Zoin Synthetic Labs Data Scientist interview invitation.'}
OTHER={'uid':9,'message_id':'test2','sender':'x@example.invalid','subject':'Newsletter','body_text':'A weekend sale.'}

class MailWorkflowTests(unittest.TestCase):
 def test_stages_deduplicate_and_filter_before_model(self):
  fetch=Mock(return_value={'messages':[GOOD,GOOD,OTHER],'next_before_uid':None,'has_more':False})
  with patch('services.mail_workflow.match_mail_for_job',return_value=[{'message_uid':10,'review_required':True}]) as model:
   result=run_mail_workflow(APP,'private@example.invalid','not-persisted',fetcher=fetch)
  model.assert_called_once_with(APP,[GOOD])
  self.assertEqual([s['stage'] for s in result['steps']],['job_date_search','deduplicate','identity_filter','groq_classify_and_validate_evidence','user_review'])
  self.assertEqual(result['status_updates_applied'],0)
  self.assertNotIn('not-persisted',repr(result))
 def test_no_candidates_skip_model(self):
  fetch=Mock(return_value={'messages':[OTHER],'next_before_uid':None,'has_more':False})
  with patch('services.mail_workflow.match_mail_for_job') as model:
   result=run_mail_workflow(APP,'a','b',fetcher=fetch)
  model.assert_not_called(); self.assertEqual(result['matches'],[])
 def test_company_substring_does_not_match(self):
  self.assertEqual(shortlist({'company':'Meta','job_title':'Analyst'},[OTHER | {'body_text':'metadata analyst interview'}]),[])
 def test_model_failure_does_not_advance_page(self):
  fetch=Mock(return_value={'messages':[GOOD],'next_before_uid':10,'has_more':True})
  with patch('services.mail_workflow.match_mail_for_job',side_effect=AIError('Retry later',429,60)):
   with self.assertRaises(AIError):run_mail_workflow(APP,'a','b',fetcher=fetch)
 def test_found_job_uses_submission_date_for_inbox_search(self):
  from datetime import datetime,timezone,timedelta
  client=Mock();client.select.return_value=('OK',[]);client.uid.return_value=('OK',[b''])
  submitted=datetime.now(timezone.utc)-timedelta(days=20)
  expected=submitted-timedelta(days=14)
  month=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')[expected.month-1]
  with patch('services.imap_client._connect',return_value=client):
   fetch_job_messages('a','b',APP|{'submitted_at':submitted.isoformat()})
  self.assertIn(f'SINCE {expected.day:02d}-{month}-{expected.year}',client.uid.call_args.args[-1])
 def test_imap_recent_filter_peek_and_descending_pages(self):
  client=Mock();client.select.return_value=('OK',[])
  raw=b'From: test@example.invalid\r\nSubject: ZOIN-TEST-26\r\nMessage-ID: <test>\r\n\r\nData Scientist interview'
  def uid(command,*args):
   return ('OK',[b'1 2 3 4 5 6 7']) if command=='search' else ('OK',[(b'meta',raw)])
  client.uid.side_effect=uid
  with patch('services.imap_client._connect',return_value=client):
   page=fetch_job_messages('a','b',APP,limit=5)
   self.assertEqual([m['uid'] for m in page['messages']],[7,6,5,4,3]);self.assertEqual(page['next_before_uid'],3)
   page2=fetch_job_messages('a','b',APP,before_uid=3,limit=5)
   self.assertEqual([m['uid'] for m in page2['messages']],[2,1]);self.assertFalse(page2['has_more'])
  self.assertTrue(all('SINCE' in c.args[-1] and 'TEXT "ZOIN-TEST-26"' in c.args[-1] for c in client.uid.call_args_list if c.args[0]=='search'))
  self.assertTrue(all(c.args[-1]=='(BODY.PEEK[]<0.65536>)' for c in client.uid.call_args_list if c.args[0]=='fetch'))
  client.select.assert_called_with('INBOX',readonly=True)

class MailAPITests(unittest.TestCase):
 def test_unknown_or_other_owner_job_never_touches_mailbox(self):
  from fastapi import Request
  from fastapi.testclient import TestClient
  from main import create_app
  from core.auth import require_user
  app=create_app()
  def user(request:Request):request.state.access_token='fixture-token';return 'owner'
  app.dependency_overrides[require_user]=user
  database=Mock();database.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data=[]
  with patch('routers.analysis.user_client',return_value=database),patch('routers.analysis.run_mail_workflow') as workflow:
   response=TestClient(app).post('/api/v1/applications/11111111-1111-4111-8111-111111111111/mail-search',json={'email_address':'test@example.com','app_password':'abcdefghijklmnop'})
  self.assertEqual(response.status_code,404);workflow.assert_not_called()
  database.table.return_value.select.return_value.eq.return_value.eq.assert_called_with('user_id','owner')
