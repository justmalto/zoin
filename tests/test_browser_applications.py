import os,sys,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services.browser_applications import PublicTransport,LiveBrowserAdapter,FormBlocked,greenhouse_reference,public_questions,question_definitions

class PublicBrowserPolicyTests(unittest.TestCase):
 def test_greenhouse_only_uses_observed_board_and_job_ids(self):
  self.assertEqual(greenhouse_reference('https://job-boards.greenhouse.io/example/jobs/123'),('example','123'))
  self.assertEqual(greenhouse_reference('https://boards.greenhouse.io/embed/job_app?for=example&token=123'),('example','123'))
  self.assertIsNone(greenhouse_reference('https://greenhouse.io.evil.test/example/jobs/123'))
  self.assertIsNone(greenhouse_reference('https://boards.greenhouse.io/embed/job_app?for=../../admin&token=123'))
 def test_public_api_read_is_not_used_for_submission(self):
  payload={'questions':[{'label':'Work eligible?','fields':[{'name':'question_1','values':[{'label':'Yes','value':1}]}]}]}
  with patch('services.browser_applications.fetch_posting',return_value=(json.dumps(payload),'unused')) as fetch:
   self.assertIn('question_1',public_questions('https://job-boards.greenhouse.io/example/jobs/123'))
   self.assertTrue(fetch.call_args.args[0].endswith('/jobs/123?questions=true'))
 def request(self,url='https://example.com/apply',method='GET'):
  route=Mock();route.request=SimpleNamespace(url=url,method=method,resource_type='document',post_data_buffer=None,all_headers=lambda:{'User-Agent':'Test'})
  return route
 def test_inspection_blocks_post_before_any_connection(self):
  transport=PublicTransport();route=self.request(method='POST')
  with patch('services.browser_applications.public_target') as target,patch('services.browser_applications.http.client.HTTPSConnection') as connection:
   transport.handle(route);route.abort.assert_called_once();target.assert_not_called();connection.assert_not_called()
 def test_private_destination_is_rejected_before_connection(self):
  transport=PublicTransport();route=self.request('https://127.0.0.1/internal')
  with patch('services.browser_applications.public_target',side_effect=ValueError('private')),patch('services.browser_applications.http.client.HTTPSConnection') as connection:
   transport.handle(route);connection.assert_not_called();route.abort.assert_called_once()
 def test_validated_ip_is_pinned_and_reused_not_resolved_by_browser(self):
  transport=PublicTransport();route=self.request();response=Mock();response.status=200;response.read1.side_effect=[b'<html>Safe</html>',b'',b'<html>Safe</html>',b''];response.getheaders.return_value=[('Content-Type','text/html'),('Content-Length','17')]
  connection=Mock();connection.getresponse.return_value=response
  with patch('services.browser_applications.public_target',return_value=('example.com','93.184.216.34',route.request.url)) as target,patch('services.browser_applications.http.client.HTTPSConnection',return_value=connection),patch('services.browser_applications.socket.create_connection') as connect:
   transport.handle(route);connection._create_connection(('example.com',443),5);connect.assert_called_once_with(('93.184.216.34',443),5,None)
   transport.handle(route);target.assert_called_once();self.assertEqual(route.fulfill.call_count,2)
 def test_writes_restricted_to_observed_form_origin_or_provider(self):
  transport=PublicTransport();transport.allow_writes=True;transport.form_origin=('https','employer.example')
  self.assertTrue(transport.write_allowed('https://employer.example/apply','POST'));self.assertFalse(transport.write_allowed('https://attacker.example/collect','POST'))
  transport.greenhouse=True
  self.assertTrue(transport.write_allowed('https://job-boards.greenhouse.io/example/jobs/1','POST'))
  self.assertFalse(transport.write_allowed('https://greenhouse.io.evil.test/collect','POST'))
 def test_custom_consent_and_extra_document_have_specific_reasons(self):
  base={'key':'consent','label':'I agree','kind':'checkbox','required':True,'options':[],'combo':False}
  for change,code in [({},'CONSENT_OR_CUSTOM_CONTROL'),({'key':'cover_letter','label':'Cover letter','kind':'file'},'ADDITIONAL_DOCUMENT_REQUIRED')]:
   with self.assertRaises(FormBlocked) as raised:question_definitions([base|change],{'url':'https://example.com/job','company':'Test'})
   self.assertEqual(raised.exception.code,code)

@unittest.skipUnless(os.environ.get('RUN_PLAYWRIGHT_MOCK')=='1','Opt-in Chromium fixture')
class HostedFormBrowserTests(unittest.TestCase):
 def setUp(self):
  self.records=[];self.login=False;self.captcha=False
  self.job={'url':'https://employer.example/apply','company':'Synthetic employer','location':'India'}
  self.adapter=LiveBrowserAdapter()
  self.target=patch('services.browser_applications.public_target',side_effect=lambda url:('employer.example','93.184.216.34',url));self.target.start()
  parent=self
  def serve(transport,route):
   req=route.request
   if not transport.write_allowed(req.url,req.method):route.abort();return
   if req.method=='POST':
    parent.records.append(req.post_data_buffer)
    route.fulfill(status=200,content_type='text/html',body='<h1>Thank you for applying</h1><p>Your application has been received.</p>');return
   if parent.login:html='<label>Password<input type="password" required></label>'
   elif parent.captcha:html='<h1>Verify you are human</h1>'
   else:html='''<form method="post" action="/apply" enctype="multipart/form-data"><label>First name<input id="first_name" name="first_name" required></label><label>Email<input type="email" id="email" name="email" required></label><label>Work eligible?<select id="eligible" name="eligible" required><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option></select></label><label>Resume<input id="resume" type="file" name="resume" required style="display:none"></label><button>Submit application</button></form>'''
   route.fulfill(status=200,content_type='text/html',body=html)
  self.route=patch.object(PublicTransport,'handle',serve);self.route.start()
 def tearDown(self):self.route.stop();self.target.stop()
 def test_real_chromium_inspection_and_final_submit_on_controlled_hosted_form(self):
  fields=self.adapter.questions(self.job)
  self.assertEqual([q['key'] for q in fields],['first_name','email','eligible']);self.assertEqual(fields[-1]['options'],['Yes','No']);self.assertEqual(self.records,[])
  result=self.adapter.submit(self.job,{'name':'Synthetic Applicant','email':'synthetic@example.test'},{'name':'resume.txt','mime_type':'text/plain','bytes':b'Synthetic resume only'},answers={'first_name':'Synthetic','email':'synthetic@example.test','eligible':'Yes'})
  self.assertEqual(result['state'],'submitted');self.assertEqual(len(self.records),1);self.assertIn(b'Synthetic resume only',self.records[0])
 def test_login_and_captcha_pause_without_upload_or_submit(self):
  for state,code in [('login','LOGIN_REQUIRED'),('captcha','CAPTCHA_OR_ACCESS_CHECK')]:
   self.login=state=='login';self.captcha=state=='captcha'
   with self.assertRaises(FormBlocked) as raised:self.adapter.questions(self.job)
   self.assertEqual(raised.exception.code,code)
  self.assertEqual(self.records,[])
 def test_missing_answer_does_not_submit(self):
  with self.assertRaises(FormBlocked) as raised:self.adapter.submit(self.job,{'email':'synthetic@example.test'},{'name':'resume.txt','mime_type':'text/plain','bytes':b'Synthetic'},answers={})
  self.assertEqual(raised.exception.code,'MISSING_ANSWER');self.assertEqual(self.records,[])
