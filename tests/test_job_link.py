import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services import job_link
from services.groq_client import AIError

DESCRIPTION = 'Build analytical models using Python. Prepare datasets and evaluate machine learning models. Collaborate with business teams.'

def fixture(**changes):
    job = {'@type':'JobPosting','title':'Data Scientist','hiringOrganization':{'name':'Example Labs'},'description':'<p>'+DESCRIPTION+'</p>','identifier':{'value':'DS-42'},'employmentType':'FULL_TIME','jobLocation':{'address':{'addressLocality':'Bengaluru','addressCountry':'IN'}}}
    job.update(changes)
    return '<script type="application/ld+json">'+json.dumps({'@graph':[job]})+'</script><nav>unrelated navigation</nav>'

class JobLinkTests(unittest.TestCase):
    def test_structured_fields_without_ai(self):
        with patch.object(job_link,'extract_job') as ai:
            data=job_link.parse_posting(fixture(),'https://example.com/job')
        self.assertEqual(data['company'],'Example Labs')
        self.assertEqual(data['job_title'],'Data Scientist')
        self.assertEqual(data['job_id'],'DS-42')
        self.assertEqual(data['location'],'Bengaluru, IN')
        self.assertEqual(data['job_description'],DESCRIPTION)
        ai.assert_not_called()

    def test_fallback_uses_only_visible_page_text(self):
        with patch.object(job_link,'extract_job',return_value={'job_title':'Analyst','company':'Example'}) as ai:
            data=job_link.parse_posting('<script>secret fake content</script><main>'+DESCRIPTION+'</main>','https://example.com/job')
        self.assertEqual(data['job_title'],'Analyst')
        self.assertNotIn('secret',ai.call_args.args[0])

    def test_greenhouse_published_title_avoids_ai(self):
        html='<head><title>Job Application for Data Scientist at Example Labs</title></head><div class="job__location"><svg></svg>London</div><main>'+DESCRIPTION+'</main>'
        with patch.object(job_link,'extract_job') as ai:
            data=job_link.parse_posting(html,'https://job-boards.eu.greenhouse.io/example/jobs/12345')
        self.assertEqual(data['company'],'Example Labs')
        self.assertEqual(data['job_title'],'Data Scientist')
        self.assertEqual(data['job_id'],'12345')
        self.assertEqual(data['location'],'London')
        self.assertNotIn('Job Application',data['job_description'])
        ai.assert_not_called()

    def test_peoplestrong_imports_public_data_without_ai(self):
        item = {'requisitionCode': 'ABG111940', 'requisitionStatus': 'OPEN',
                'jobTitle': 'Data Scientist', 'jobDescription': '<p>'+DESCRIPTION+'</p>',
                'organizationUnitComplete': 'Finance>Example Limited>Analytics',
                'locationHierarchyComplete': 'India>Maharashtra>Goregaon'}
        url = 'https://abgcareers.peoplestrong.com/job/detail/ABG111940?reference=LINKEDIN'
        with patch.object(job_link, 'public_target'), patch.object(job_link, 'fetch_posting', return_value=(json.dumps({'response': item}), url)) as fetch, patch.object(job_link, 'extract_job') as ai:
            data = job_link.import_job_link(url)
        self.assertEqual(data['company'], 'Example Limited')
        self.assertEqual(data['location'], 'Goregaon, Maharashtra, India')
        self.assertEqual(data['job_id'], 'ABG111940')
        self.assertEqual(data['job_description'], DESCRIPTION)
        self.assertEqual(data['source_url'], url)
        self.assertTrue(fetch.call_args.kwargs['json_response'])
        ai.assert_not_called()

    def test_peoplestrong_rejects_mismatch_closed_and_invalid_responses(self):
        for body in ['not json', '[]', '{"response":null}', json.dumps({'response': {'requisitionCode':'OTHER'}}), json.dumps({'response': {'requisitionCode':'ABG111940','requisitionStatus':'CLOSED','jobDescription':DESCRIPTION}})]:
            with self.subTest(body=body), patch.object(job_link, 'public_target'), patch.object(job_link, 'fetch_posting', return_value=(body, 'https://example.com')):
                with self.assertRaises(AIError):
                    job_link.import_job_link('https://abgcareers.peoplestrong.com/job/detail/ABG111940')

    def test_peoplestrong_adapter_does_not_match_lookalikes(self):
        for url in ['https://peoplestrong.com.evil.example/job/detail/ABC', 'https://abgcareers.peoplestrong.com/job/detail/ABC/extra']:
            with patch.object(job_link, 'fetch_posting') as fetch:
                self.assertIsNone(job_link.peoplestrong_posting(url))
                fetch.assert_not_called()

    def test_linkedin_search_and_slug_links_normalize(self):
        with patch.object(job_link, 'public_target'):
            for url in ['https://www.linkedin.com/jobs/search-results/?currentJobId=4480650624&trackingId=abc', 'https://in.linkedin.com/jobs/view/data-scientist-at-setu-4480650624/']:
                self.assertEqual(job_link.normalize_job_url(url), 'https://www.linkedin.com/jobs/view/4480650624/')
            for url in ['https://www.linkedin.com/jobs/search/?keywords=analyst', 'https://www.linkedin.com/jobs/search/?currentJobId=12&currentJobId=34']:
                with self.assertRaises(AIError): job_link.normalize_job_url(url)
        url = 'https://linkedin.com.evil.example/jobs/search/?currentJobId=123'
        self.assertEqual(job_link.normalize_job_url(url), url)
        with patch.object(job_link, 'public_target', side_effect=AIError('Invalid',422)):
            with self.assertRaises(AIError): job_link.normalize_job_url('https://user:pass@linkedin.com/jobs/view/123')

    def test_linkedin_encoded_description_and_null_attributes(self):
        html = '<div class><script type></script></div>' + fixture(description='&lt;p&gt;'+DESCRIPTION+'&lt;/p&gt;')
        result = job_link.parse_posting(html, 'https://www.linkedin.com/jobs/view/4480650624/')
        self.assertEqual(result['job_description'], DESCRIPTION)
        self.assertEqual(result['job_id'], 'DS-42')
        html = fixture(identifier={})
        self.assertEqual(job_link.parse_posting(html, 'https://www.linkedin.com/jobs/view/4480650624/')['job_id'], '4480650624')

    def test_linkedin_public_sections_exclude_recommended_jobs(self):
        html = '<h1 class="topcard__title">Scientist</h1><a class="topcard__org-name-link">Setu</a><span class="topcard__flavor--bullet">Bengaluru</span><div class="show-more-less-html__markup"><div><p>'+DESCRIPTION+'</p></div></div><main>Unrelated recommended jobs</main>'
        with patch.object(job_link, 'extract_job') as ai:
            result = job_link.parse_posting(html, 'https://www.linkedin.com/jobs/view/4480650624/')
        self.assertEqual(result['company'], 'Setu')
        self.assertEqual(result['location'], 'Bengaluru')
        self.assertEqual(result['job_description'], DESCRIPTION)
        ai.assert_not_called()

    def test_linkedin_login_page_is_not_a_job(self):
        with patch.object(job_link, 'extract_job') as ai:
            with self.assertRaises(AIError): job_link.parse_posting('<main>'+('Sign in to LinkedIn. '*20)+'</main>', 'https://www.linkedin.com/jobs/view/123/')
            ai.assert_not_called()

    def test_cleaning_removes_clutter_preserving_later_requirements(self):
        text = 'Apply now\nAbout Example\nWe are an amazing company.\nAbout the Role\n'+DESCRIPTION+'\nDisclaimer\nLegal boilerplate.\nQualifications\nPython and SQL required.\nBenefits\nHealth insurance.\nSalary\nINR 10–20 lakh\nShare this job'
        result = job_link.clean_description(text, 'Example')
        self.assertNotIn('amazing company', result)
        self.assertNotIn('Legal boilerplate', result)
        self.assertNotIn('Apply now', result)
        for required in [DESCRIPTION, 'Python and SQL required.', 'Health insurance.', 'INR 10–20 lakh']:
            self.assertIn(required, result)

    def test_cleaning_keeps_unrecognized_text_and_meaningful_duplicates(self):
        text = 'Build privacy models for fraud detection.\nResponsibilities\nPython\nPreferred Skills\nPython'
        self.assertEqual(job_link.clean_description(text), text)
        self.assertEqual(job_link.clean_description(job_link.clean_description(text)), text)

    def test_unheaded_company_intro_and_privacy_leave_compensation(self):
        text = 'Example is a media intelligence company.\nABOUT THE ROLE\n'+DESCRIPTION+'\nWHO ARE YOU\nExpert Python skills.\n$150,000 - $185,000 a year\nExample is an equal opportunity employer.\nExample may collect personal information directly from you, as a job applicant.\n'+r'\n'+'\n--------'
        result = job_link.clean_description(text, 'Example')
        self.assertTrue(result.startswith('ABOUT THE ROLE'))
        self.assertIn('$150,000 - $185,000 a year', result)
        self.assertIn('Expert Python skills.', result)
        self.assertNotIn('personal information', result)
        self.assertNotIn('--------', result)
        self.assertNotIn(r'\n', result)

    def test_greenhouse_description_section_excludes_page_chrome(self):
        html = '<head><title>Job Application for Scientist at Example</title></head><main>Recommended jobs<div class="job__description body">'+DESCRIPTION+'</div>Get job alerts</main>'
        result = job_link.parse_posting(html, 'https://job-boards.greenhouse.io/example/jobs/123')
        self.assertEqual(result['job_description'], DESCRIPTION)

    def test_private_and_mixed_dns_are_rejected(self):
        for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:127.0.0.1']:
            with self.subTest(ip=ip), patch.object(job_link.socket,'getaddrinfo',return_value=[(2,1,6,'',(ip,443))]):
                with self.assertRaises(AIError):job_link.public_target('https://example.com/job')
        with patch.object(job_link.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('10.0.0.1',443))]):
            with self.assertRaises(AIError):job_link.public_target('https://example.com/job')

    def test_bad_urls_do_not_resolve(self):
        for url in ['file:///etc/passwd','http://example.com','https://user:pass@example.com','https://example.com:444','https://example.com/\r\nX:test']:
            with self.subTest(url=url),patch.object(job_link.socket,'getaddrinfo') as dns:
                with self.assertRaises(AIError):job_link.public_target(url)
                dns.assert_not_called()

    def test_redirect_is_revalidated(self):
        reply=Mock(status=302);reply.getheader.return_value='https://127.0.0.1/private'
        conn=Mock();conn.getresponse.return_value=reply
        with patch.object(job_link.socket,'getaddrinfo',side_effect=[[(2,1,6,'',('8.8.8.8',443))],[(2,1,6,'',('127.0.0.1',443))]]),patch.object(job_link.http.client,'HTTPSConnection',return_value=conn):
            with self.assertRaises(AIError):job_link.fetch_posting('https://example.com/job')
        self.assertEqual(conn.request.call_count,1)
        conn.close.assert_called()

    def test_pins_ip_without_changing_tls_hostname(self):
        reply=Mock(status=200);reply.getheader.side_effect=lambda name,default=None: {'Content-Type':'text/html'}.get(name,default)
        reply.read1.side_effect=[b'<html>text</html>',b'']
        conn=Mock();conn.getresponse.return_value=reply
        with patch.object(job_link.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(job_link.http.client,'HTTPSConnection',return_value=conn) as factory,patch.object(job_link.socket,'create_connection') as connect:
            job_link.fetch_posting('https://example.com/job')
            conn._create_connection(('example.com',443),5)
            connect.assert_called_once_with(('8.8.8.8',443),5,None)
            self.assertEqual(factory.call_args.args[0],'example.com')

    def test_blocked_page_size_and_timeout(self):
        for status,kind,parts in [(403,'text/html',[]),(200,'application/pdf',[]),(200,'text/html',[b'x'*(job_link.MAX_BYTES+1)])]:
            reply=Mock(status=status);reply.getheader.side_effect=lambda name,default=None: {'Content-Type':kind}.get(name,default);reply.read1.side_effect=parts
            conn=Mock();conn.getresponse.return_value=reply
            with self.subTest(status=status,kind=kind),patch.object(job_link,'public_target',return_value=('example.com','8.8.8.8','https://example.com/job')),patch.object(job_link.http.client,'HTTPSConnection',return_value=conn):
                with self.assertRaises(AIError):job_link.fetch_posting('https://example.com/job')

    def test_multiple_jobs_and_js_shell_are_rejected(self):
        with self.assertRaises(AIError):job_link.parse_posting('<html>Enable JavaScript</html>','https://example.com')
        with self.assertRaises(AIError):job_link.parse_posting(fixture()+fixture(),'https://example.com')


class JobLinkApiTests(unittest.TestCase):
    def test_authentication_and_response_contract(self):
        from fastapi.testclient import TestClient
        from core.auth import require_user
        from main import create_app
        app=create_app()
        client=TestClient(app)
        with patch('routers.analysis.import_job_link') as fetch:
            response=client.post('/api/v1/analysis/job-link',json={'url':'https://example.com/job'})
            self.assertEqual(response.status_code,401)
            fetch.assert_not_called()
        app.dependency_overrides[require_user]=lambda:'test-user'
        expected={'job_title':'Analyst','company':'Example','job_description':DESCRIPTION,'source_url':'https://example.com/job','warnings':[]}
        with patch('routers.analysis.import_job_link',return_value=expected):
            response=client.post('/api/v1/analysis/job-link',json={'url':'https://example.com/job'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json(),expected)
        response=client.post('/api/v1/analysis/job-link',json={'url':''})
        self.assertEqual(response.status_code,422)
        app.dependency_overrides.clear()


if __name__=="__main__":unittest.main()
