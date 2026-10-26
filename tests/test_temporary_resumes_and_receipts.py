import sys,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from fastapi import Request
from fastapi.testclient import TestClient
from core.auth import require_user
from main import create_app
from services.mail_receipts import receipt_matches_job,apply_received_mail

class TemporaryResumeTests(unittest.TestCase):
 def setUp(self):
  app=create_app()
  def user(request:Request):request.state.access_token='fixture';return 'owner'
  app.dependency_overrides[require_user]=user;self.client=TestClient(app)
 def test_temporary_preview_has_no_database_or_storage_writes(self):
  with patch('routers.analysis.match_resume',return_value={'match_score':70}),patch('routers.analysis.user_client') as db:
   response=self.client.post('/api/v1/analysis/resume-preview',data={'job_description':'Python and machine learning required'},files={'file':('tailored.txt',b'Built Python machine learning models.','text/plain')})
  self.assertEqual(response.status_code,200);self.assertTrue(response.json()['temporary']);db.assert_not_called()
 def test_empty_or_unsupported_preview_never_calls_model(self):
  with patch('routers.analysis.match_resume') as model:
   for content,mime in [(b'','text/plain'),(b'fake','image/png')]:
    response=self.client.post('/api/v1/analysis/resume-preview',data={'job_description':'Python and machine learning required'},files={'file':('bad',content,mime)})
    self.assertIn(response.status_code,[400,415])
  model.assert_not_called()
 def test_ten_saved_resumes_returns_replacement_instruction(self):
  db=Mock();query=db.table.return_value.select.return_value
  query.eq.return_value.eq.return_value.execute.return_value.data=[]
  query.eq.return_value.execute.return_value.data=[{'id':str(n)} for n in range(10)]
  with patch('routers.resumes.user_client',return_value=db):
   response=self.client.post('/api/v1/resumes',files={'file':('new.txt',b'Python machine learning resume.','text/plain')})
  self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail']['code'],'resume_limit');db.storage.from_.assert_not_called()
 def test_replace_other_owner_is_denied_before_storage_upload(self):
  db=Mock();db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data=[]
  with patch('routers.resumes.user_client',return_value=db):
   response=self.client.post('/api/v1/resumes?replace_id=11111111-1111-4111-8111-111111111111',files={'file':('new.txt',b'Python machine learning resume.','text/plain')})
  self.assertEqual(response.status_code,404);db.storage.from_.assert_not_called()
 def test_new_applications_cannot_claim_applied(self):
  with patch('routers.applications.user_client') as db:
   response=self.client.post('/api/v1/applications',json={'company':'Example','job_title':'Analyst','job_description':'Python analytical models required','initial_status':'applied'})
  self.assertEqual(response.status_code,422);db.assert_not_called()

class ReceiptTests(unittest.TestCase):
 def setUp(self):
  self.app={'id':'job','company':'Example','job_title':'Data Scientist','job_id':'JOB-42','status':'found'}
  self.receipt={'message_uid':42,'message_id':'<test>','category':'confirmation','suggested_status':'applied','confidence':0.97,'company':'Example','job_title':'Data Scientist','job_id':'JOB-42','evidence':['Your application was received.']}
 def test_only_strong_identity_matched_confirmation_auto_applies(self):
  self.assertTrue(receipt_matches_job(self.app,self.receipt))
  for changes in [{'category':'interview'},{'job_id':'WRONG'},{'confidence':0.4},{'evidence':[]},{'evidence':['Please apply for this role.']},{'evidence':['We have not received your application.']},{'job_id':None,'job_title':'Other role'}]:
   self.assertFalse(receipt_matches_job(self.app,self.receipt|changes))
 def test_auto_update_uses_owner_scoped_receipt_rpc(self):
  db=Mock();db.rpc.return_value.execute.return_value.data={'application':self.app|{'status':'applied','receipt_confirmed_at':'today'},'updated':True}
  result=apply_received_mail(db,self.app,[self.receipt]);self.assertEqual(result['status_updates_applied'],1)
  self.assertEqual(db.rpc.call_args.args[0],'apply_application_receipt')
 def test_no_receipt_means_no_status_write(self):
  db=Mock();result=apply_received_mail(db,self.app,[self.receipt|{'category':'other'}]);db.rpc.assert_not_called();self.assertEqual(result['application']['status'],'found')
