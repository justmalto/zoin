"""Opt-in real Chromium tests against an isolated localhost employer form only."""
import hashlib
import os
import sys
import tempfile
import unittest
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services.playwright_apply import LocalMockAdapter
from services import agent_applications as agent, discovery_store as store

@unittest.skipUnless(os.environ.get('RUN_PLAYWRIGHT_MOCK')=='1','Opt-in local Chromium test')
class RealMockBrowserTests(unittest.TestCase):
    def setUp(self):
        self.records=[];self.events=[];self.captcha=False;self.with_question=False
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                question='<label>Eligible to work in India?<select name="eligible" data-agent-question required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>' if parent.with_question else ''
                captcha='<p>CAPTCHA verification required</p>' if parent.captcha else ''
                body=(f'<!doctype html><html><body>{captcha}<form method="post" action="/apply" enctype="multipart/form-data">'
                      '<label>Full name<input name="name" required></label><label>Email<input name="email" type="email" required></label>'
                      f'<label>Resume<input name="resume" type="file" required></label>{question}<button>Submit application</button></form></body></html>').encode()
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']))
                message=BytesParser(policy=default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
                fields={}
                for part in message.iter_parts():fields[part.get_param('name',header='content-disposition')]=part.get_payload(decode=True)
                parent.records.append(fields);parent.events.append('form_submitted')
                response=b'<!doctype html><html><body><p role="status">Application received: mock-receipt-001</p></body></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.adapter=LocalMockAdapter(self.origin)
        self.job={'source':'local_mock','url':self.origin+'/apply'}
        self.resume={'name':'synthetic-resume.txt','mime_type':'text/plain','bytes':b'Synthetic applicant. Patient care experience.'}
        self.applicant={'name':'Synthetic Applicant','email':'synthetic@example.test'}
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def test_real_form_fill_upload_and_receipt(self):
        self.adapter.preflight()
        result=self.adapter.submit(self.job,self.applicant,self.resume)
        self.assertEqual(result,{'state':'submitted','reference':'mock-receipt-001'})
        self.assertEqual(len(self.records),1)
        self.assertEqual(self.records[0]['name'],b'Synthetic Applicant')
        self.assertEqual(self.records[0]['email'],b'synthetic@example.test')
        self.assertEqual(self.records[0]['resume'],self.resume['bytes'])
    def test_captcha_stops_without_submitting(self):
        self.captcha=True
        result=self.adapter.submit(self.job,self.applicant,self.resume)
        self.assertEqual(result['state'],'needs_user_action')
        self.assertEqual(self.records,[])
    def test_tracking_precedes_real_browser_submission_and_retry_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=SimpleNamespace(discovery_db_path=str(Path(directory)/'agent.sqlite3'),discovery_mock_origin=self.origin,supabase_resume_bucket='resumes')
            rid=str(uuid4());aid=str(uuid4())
            job=self.job|{'title':'Registered Nurse','company':'Local mock only','description':'Provide patient care and maintain clinical records. Collaborate with the clinical team.',
                'resume_id':rid,'resume_hash':hashlib.sha256(self.resume['bytes']).hexdigest(),
                'assessment':{'method':'profession_independent_requirement_coverage','score':90,'mandatory_blockers':[],'eligible_for_automation':True}}
            client=Mock();rq=Mock();aq=Mock()
            for query in [rq,aq]:
                for method in ['select','eq','update']:getattr(query,method).return_value=query
            rq.execute.return_value=SimpleNamespace(data=[{'id':rid,'file_name':self.resume['name'],'mime_type':self.resume['mime_type'],'file_path':'mock/resume','resume_text':self.resume['bytes'].decode()}])
            aq.execute.return_value=SimpleNamespace(data=[])
            client.table.side_effect=lambda table:rq if table=='resumes' else aq
            def create(*args):
                self.events.append('tracked_record_created')
                return SimpleNamespace(execute=lambda:SimpleNamespace(data={'id':aid,'status':'found'}))
            client.rpc.side_effect=create
            client.storage.from_.return_value.download.return_value=self.resume['bytes']
            with patch.object(store,'get_settings',return_value=settings),patch('core.config.get_settings',return_value=settings),patch.object(agent,'find_recommendation',return_value=(job,{'matching_threshold':80,'automation_level':'existing_resume'})):
                result=agent.submit('mock-owner','run',job['url'],client,self.applicant,adapter=self.adapter)
                self.assertEqual(result['state'],'submitted');self.assertEqual(result['application_id'],aid)
                second=agent.submit('mock-owner','run',job['url'],client,self.applicant,adapter=self.adapter)
                self.assertTrue(second['already_recorded'])
                self.assertEqual(self.events,['tracked_record_created','form_submitted'])
                self.assertEqual(len(self.records),1)
                self.assertEqual(agent.attempts('different-owner'),[])
                self.assertNotIn('status',aq.update.call_args.args[0])

    def test_questions_are_inspected_before_submit_and_confirmed_answers_fill(self):
        self.with_question=True
        job=self.job|{'company':'Synthetic employer','location':'India'}
        questions=self.adapter.questions(job)
        self.assertEqual(questions[0]['label'],'Eligible to work in India?')
        self.assertEqual(questions[0]['options'],['Yes','No'])
        self.assertEqual(questions[0]['context']['employer'],'Synthetic employer')
        self.assertEqual(self.records,[])
        blocked=self.adapter.submit(job,self.applicant,self.resume)
        self.assertEqual(blocked['state'],'needs_user_action');self.assertEqual(self.records,[])
        result=self.adapter.submit(job,self.applicant,self.resume,answers={'eligible':'Yes'})
        self.assertEqual(result['state'],'submitted');self.assertEqual(self.records[0]['eligible'],b'Yes')
