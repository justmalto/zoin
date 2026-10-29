import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from fastapi import HTTPException
from services import form_inspection as inspection,discovery_store as store
from services.browser_applications import FormBlocked

class InspectionTests(unittest.TestCase):
 def setUp(self):
  self.directory=tempfile.TemporaryDirectory();self.config=patch.object(store,'get_settings',return_value=SimpleNamespace(discovery_db_path=str(Path(self.directory.name)/'db')));self.config.start()
  self.job={'id':'job','user_id':'owner','status':'found','company':'Synthetic','job_title':'Engineer','job_description':'A synthetic role description.','application_url':'https://example.com/job'}
  self.application=patch.object(inspection,'application',return_value=self.job);self.application.start()
  self.adapter=Mock();self.factory=patch.object(inspection,'application_browser',return_value=self.adapter);self.factory.start()
 def tearDown(self):self.factory.stop();self.application.stop();self.config.stop();self.directory.cleanup()
 def test_read_only_inspection_registers_questions_without_submitting(self):
  self.adapter.questions.return_value=[{'key':'eligible','label':'Eligible?'}]
  with patch.object(inspection,'register',return_value={'state':'needs_answers','questions':[]}) as register:
   result=inspection.inspect(Mock(),'owner','job')
   self.assertEqual(result['inspection']['state'],'inspected');register.assert_called_once();self.adapter.submit.assert_not_called()
 def test_actual_failure_reason_persists_and_is_owner_private(self):
  self.adapter.questions.side_effect=FormBlocked('LOGIN_REQUIRED','The employer requires login.')
  with patch('services.application_questions.read',return_value={'questions':[]}):
   result=inspection.inspect(Mock(),'owner','job')
  self.assertEqual(result['inspection']['code'],'LOGIN_REQUIRED');self.assertIsNone(inspection.latest('other',self.job));self.adapter.submit.assert_not_called()
 def test_previously_submitted_job_cannot_restart_inspection(self):
  self.job['submitted_at']='2026-10-29T00:00:00Z'
  with self.assertRaises(HTTPException):inspection.inspect(Mock(),'owner','job')
  self.adapter.questions.assert_not_called()
