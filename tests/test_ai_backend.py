import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import httpx
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from core.auth import require_user
from core.config import get_settings
from main import create_app
from schemas.ai import JobExtraction, ResumeAssessment, MailMatches
from services import groq_client, recommendation_service, email_classifier, imap_client
from services.groq_client import AIError


def assessment():
    return ResumeAssessment.model_validate({
        'summary': 'Test summary',
        'criteria': [{'category': cat, 'requirement_id': 'J001', 'coverage': 'met', 'resume_evidence_ids': ['R001'], 'explanation': 'Evidence supplied'} for cat in recommendation_service.WEIGHTS],
        'strengths': [{'claim': 'Python models', 'evidence_ids': ['R001']}],
        'suggestions': ['Describe validation results'],
    })


class AIBackendTests(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings().model_copy(update={'groq_api_key': SecretStr('test-key-only')})
        groq_client._reservations.clear()

    def test_missing_key_fails_without_network(self):
        with patch.object(groq_client, 'get_settings', return_value=self.settings.model_copy(update={'groq_api_key': SecretStr('')})), patch.object(groq_client.httpx, 'post') as post:
            with self.assertRaises(AIError) as error:
                groq_client.complete_json('system', 'text', JobExtraction)
            self.assertEqual(error.exception.status_code, 503)
            post.assert_not_called()

    def test_schema_all_properties_required(self):
        schema = groq_client._strict_schema(ResumeAssessment)
        def check(obj):
            if isinstance(obj, dict):
                if obj.get('type') == 'object':
                    self.assertFalse(obj['additionalProperties'])
                    self.assertEqual(set(obj['required']), set(obj['properties']))
                for v in obj.values(): check(v)
            elif isinstance(obj, list):
                for v in obj: check(v)
        check(schema)

    def test_provider_rate_limit_has_retry_and_no_secret(self):
        response = httpx.Response(429, headers={'retry-after': '9'}, request=httpx.Request('POST', 'https://api.groq.com'))
        with patch.object(groq_client, 'get_settings', return_value=self.settings), patch.object(groq_client.httpx, 'post', return_value=response):
            with self.assertRaises(AIError) as error:
                groq_client.complete_json('system', 'text', JobExtraction)
            self.assertEqual((error.exception.status_code, error.exception.retry_after), (429,9))
            self.assertNotIn('test-key', str(error.exception))

    def test_invalid_or_truncated_provider_output_rejected(self):
        for reason, content in [('length','{}'), ('stop','[]'), ('stop','not json')]:
            groq_client._reservations.clear()
            response = httpx.Response(200, json={'choices':[{'finish_reason':reason,'message':{'content':content}}]}, request=httpx.Request('POST','https://api.groq.com'))
            with patch.object(groq_client,'get_settings',return_value=self.settings),patch.object(groq_client.httpx,'post',return_value=response):
                with self.assertRaises(AIError):groq_client.complete_json('system','text',JobExtraction)

    def test_budget_does_not_silently_overrun(self):
        groq_client._reserve(6000,7500)
        with self.assertRaises(AIError) as error:groq_client._reserve(2000,7500)
        self.assertEqual(error.exception.status_code,429)
        with self.assertRaises(AIError) as error:groq_client._reserve(8000,7500)
        self.assertEqual(error.exception.status_code,413)

    def test_score_and_evidence(self):
        result = assessment()
        result.criteria[0].coverage='partial'
        with patch.object(recommendation_service,'complete_json',return_value=result):
            score = recommendation_service.match_resume('Python required for this analytical role','Built Python models for a project')
        self.assertEqual(score['match_score'],90)
        self.assertEqual(len(score['criteria']),8)

    def test_hallucinated_resume_source_rejected(self):
        result=assessment();result.criteria[0].resume_evidence_ids=['R999']
        with patch.object(recommendation_service,'complete_json',return_value=result):
            with self.assertRaises(AIError):recommendation_service.match_resume('Python required for this analytical role','Built Python models for a project')

    def test_duplicate_rubric_categories_rejected(self):
        result=assessment();result.criteria[0].category=result.criteria[1].category
        with patch.object(recommendation_service,'complete_json',return_value=result):
            with self.assertRaises(AIError):recommendation_service.match_resume('Python required for this analytical role','Built Python models for a project')

    def test_experience_guard_does_not_invent_unsupported_partial_evidence(self):
        result=assessment()
        experience=next(item for item in result.criteria if item.category=='experience')
        experience.coverage='not_evidenced';experience.resume_evidence_ids=[]
        with patch.object(recommendation_service,'complete_json',return_value=result):
            output=recommendation_service.match_resume('2+ years of Python ML experience required','Experience\nEngineer Jan 2026 - Mar 2026\nPython models')
        checked=next(item for item in output['criteria'] if item['category']=='experience')
        self.assertEqual(checked['coverage'],'not_evidenced')
        self.assertEqual(checked['resume_evidence'],[])

    def test_full_documents_fit_compact_prompt_without_truncation(self):
        import math
        jd='Python ML required. '+('x' * 6634)
        resume='Built Python models. '+('y' * 3462)
        with patch.object(recommendation_service,'complete_json',return_value=assessment()) as model:
            recommendation_service.match_resume(jd,resume)
        call=model.call_args
        payload=json.loads(call.args[1])
        self.assertEqual(payload['job_passages']['J001'],jd)
        self.assertEqual(payload['resume_passages']['R001'],resume)
        estimate=math.ceil(len((call.args[0]+call.args[1]+json.dumps(groq_client._strict_schema(ResumeAssessment))).encode())/3)+call.kwargs['max_output_tokens']+100
        self.assertLessEqual(estimate,7500)

    def test_unknown_email_uid_rejected(self):
        output=MailMatches.model_validate({'matches':[{'message_uid':999,'confidence':1,'category':'interview','suggested_status':'interview','evidence_ids':['E1_2'],'reason':'Test','company':None,'job_title':None,'job_id':None,'interview_details':None,'deadline_text':None}]})
        with patch.object(email_classifier,'complete_json',return_value=output):
            with self.assertRaises(AIError):email_classifier.match_mail_for_job({'company':'A'},[{'uid':1,'sender':'a@example.com','subject':'Interview','body_text':'Interview invitation'}])

    def test_imap_oldest_batch_and_stale_uid(self):
        client=Mock();client.select.return_value=('OK',[])
        def uid(command,*args):
            if command=='search':return ('OK',[b'1 2 3 4 5 6 7 8 9 10'])
            return ('OK',[(b'meta',b'From: test@example.com\r\nSubject: Interview\r\nContent-Type: text/plain\r\n\r\nHello')])
        client.uid.side_effect=uid
        with patch.object(imap_client,'_connect',return_value=client):
            self.assertEqual([m.uid for m in imap_client.fetch_messages_after_uid('a','b',0,5)],[1,2,3,4,5])
            self.assertEqual(imap_client.fetch_messages_after_uid('a','b',100,5),[])
        self.assertTrue(all(call.args[-1]=='(BODY.PEEK[])' for call in client.uid.call_args_list if call.args[0]=='fetch'))

    def test_html_only_mail(self):
        msg=imap_client.email.message_from_bytes(b'Content-Type: text/html\r\n\r\n<p>Interview Friday</p><script>ignored</script>')
        self.assertIn('Interview Friday',imap_client._extract_body(msg))
        self.assertNotIn('ignored',imap_client._extract_body(msg))

    def test_experience_date_bound_does_not_count_the_gap(self):
        from services.experience import check_experience
        result=check_experience('2+ years of ML experience','Experience\nEngineer Mar 2026- present\nIntern Aug 2024 – Jan 2025\nProjects\nProject Jan 2020 - Dec 2025','2026-10-24')
        self.assertEqual(result['documented_months_upper_bound'],14)
        self.assertEqual(result['required_years'],2)

    def test_experience_overlaps_are_not_double_counted(self):
        from services.experience import check_experience
        result=check_experience('2+ years','Experience\nJan 2025 - Dec 2025\nJun 2025 - Dec 2025','2026-10-24')
        self.assertEqual(result['documented_months_upper_bound'],12)

    def test_api_denies_unauthenticated(self):
        api=TestClient(create_app())
        self.assertEqual(api.post('/api/v1/analysis/job',json={'text':'Some job description here'}).status_code,401)
        self.assertEqual(api.post('/api/v1/analysis/resume-match',json={'job_description':'Some job description here','resume_id':'11111111-1111-4111-8111-111111111111'}).status_code,401)

    def test_api_cannot_analyze_other_users_resume(self):
        app=create_app()
        def user(request:Request):request.state.access_token='test';return 'owner-a'
        app.dependency_overrides[require_user]=user
        database=Mock();database.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data=[]
        with patch('routers.analysis.user_client',return_value=database),patch('routers.analysis.match_resume') as model:
            response=TestClient(app).post('/api/v1/analysis/resume-match',json={'job_description':'Some job description here','resume_id':'11111111-1111-4111-8111-111111111111'})
        self.assertEqual(response.status_code,404);model.assert_not_called()
        database.table.return_value.select.return_value.eq.return_value.eq.assert_called_once_with('user_id','owner-a')

    def test_api_error_is_safe_and_retryable(self):
        app=create_app();app.dependency_overrides[require_user]=lambda:'owner-a'
        with patch('routers.analysis.extract_job',side_effect=AIError('Free-tier limit',429,60)):
            response=TestClient(app).post('/api/v1/analysis/job',json={'text':'Some job description here'})
        self.assertEqual(response.status_code,429);self.assertEqual(response.headers['retry-after'],'60')


if __name__=='__main__':unittest.main()
