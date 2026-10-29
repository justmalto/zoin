import sys,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services.application_questions import memory_key,validate_questions,followup_due,save,AnswerSubmission
from schemas.discovery import DiscoveryPreferences

class QuestionPolicyTests(unittest.TestCase):
 def setUp(self):
  self.question={'key':'work','label':'Have you worked here?','type':'select','options':['Yes','No'],'context':{'country':'India','employer':'A'}}
  self.job={'status':'found','created_at':(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(),'application_url':'https://example.com/job'}
  self.prefs={'found_followup_enabled':True,'found_followup_hours':24,'automation_level':'existing_resume'}
 def test_context_never_crosses_employer_country_or_options(self):
  base=memory_key(self.question)
  for change in ({'country':'US','employer':'A'},{'country':'India','employer':'B'}):self.assertNotEqual(base,memory_key(self.question|{'context':change}))
  self.assertEqual(base,memory_key(self.question|{'context':self.question['context']|{'form_url':'https://example.com/another-job'}}))
  self.assertNotEqual(base,memory_key(self.question|{'options':['Yes','No','Unsure']}))
 def test_cooldown_and_existing_submission_guards(self):
  self.assertTrue(followup_due(self.job,self.prefs))
  for hours in (50,72):self.assertFalse(followup_due(self.job,self.prefs|{'found_followup_hours':hours}))
  for change in ({'submitted_at':'today'},{'receipt_confirmed_at':'today'},{'status':'applied'}):self.assertFalse(followup_due(self.job|change,self.prefs))
  self.assertFalse(followup_due(self.job,self.prefs,[self.job['application_url']]))
  self.assertFalse(followup_due(self.job,self.prefs|{'paused':True}))
  self.assertFalse(followup_due(self.job,self.prefs|{'automation_level':'recommend'}))
 def test_only_explicit_cooldown_choices(self):
  for hours in (12,24,50,72):self.assertEqual(DiscoveryPreferences(found_followup_hours=hours).found_followup_hours,hours)
  with self.assertRaises(ValueError):DiscoveryPreferences(found_followup_hours=48)
 def test_sensitive_fields_and_invalid_definitions_block(self):
  for change in ({'label':'Enter your password'},{'type':'select','options':[]},{'context':{'secret':'x'}}):
   with self.assertRaises(ValueError):validate_questions([self.question|change])
  with self.assertRaises(ValueError):validate_questions([self.question,self.question])
 def test_answer_validation_before_transaction(self):
  from unittest.mock import patch
  client=Mock();job={'id':'job','status':'found','updated_at':'today'}
  current={'revision':'a'*64,'questions':[self.question|{'required':True}]}
  with patch('services.application_questions.application',return_value=job),patch('services.application_questions.read',return_value=current):
   for answer in ('','Maybe'):
    payload=AnswerSubmission(revision='a'*64,answers=[{'key':'work','value':answer,'remember':False}])
    with self.assertRaises(HTTPException):save(client,'owner','job',payload)
   client.rpc.assert_not_called()
   payload=AnswerSubmission(revision='a'*64,answers=[{'key':'work','value':'No','remember':False}]);save(client,'owner','job',payload)
   self.assertFalse(client.rpc.call_args.args[1]['p_answers'][0]['remember'])

class FoundWorkerTests(unittest.TestCase):
 def test_saved_found_jobs_work_without_discovery_and_do_not_replace_home(self):
  import tempfile
  from unittest.mock import patch
  from services import discovery_store as store,automation
  import hashlib
  with tempfile.TemporaryDirectory() as directory:
   settings=SimpleNamespace(discovery_db_path=str(Path(directory)/'agent.sqlite3'))
   with patch.object(store,'get_settings',return_value=settings):
    prefs=DiscoveryPreferences(automation_level='existing_resume',found_followup_enabled=True).model_dump()
    store.save_preferences('owner',prefs)
    job={'id':'app','status':'found','created_at':(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(),'company':'Mock','job_title':'Nurse','job_description':'Provide care for patients.','application_url':'https://example.com/job'}
    rq=Mock();aq=Mock();client=Mock()
    for query in (rq,aq):
     for method in ('select','eq','limit'):getattr(query,method).return_value=query
    rq.execute.return_value=SimpleNamespace(data=[{'id':'resume','file_name':'nurse.txt','resume_text':'Nurse experienced in patient care.'}])
    aq.execute.return_value=SimpleNamespace(data=[job])
    client.table.side_effect=lambda table:rq if table=='resumes' else aq
    adapter=Mock();adapter.supports.return_value=True
    with patch('services.career_agent.requirements_for',return_value={}),patch('services.career_agent.assess',return_value={'score':90,'eligible_for_automation':True}) as assess:
     runs=automation.found_followups('owner',client,prefs,adapter)
     self.assertEqual(len(runs),1);self.assertEqual(runs[0]['result']['jobs'][0]['resume_id'],'resume')
     again=automation.found_followups('owner',client,prefs,adapter)
     self.assertEqual(again[0]['id'],runs[0]['id']);self.assertEqual(assess.call_count,1)
    self.assertIsNone(store.latest('owner')['run'])
    job['submitted_at']='today'
    self.assertEqual(automation.found_followups('owner',client,prefs,adapter),[])
