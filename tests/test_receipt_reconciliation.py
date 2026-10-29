import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.mail_workflow import run_mail_workflow
from services.mail_receipts import explicit_receipts, receipt_matches_job

APP = {'id': 'job', 'status': 'found', 'company': 'Example AI', 'job_title': 'Software Engineer 1',
       'job_id': 'R-101 / 80123', 'application_url': 'https://example.ai/careers/jobs/80123',
       'submitted_at': '2026-10-29T16:30:00+00:00', 'receipt_company_candidates': 1}
MAIL = {'uid': 42, 'message_id': '<fixture>', 'sender': 'no-reply@example.ai',
        'subject': 'Thank you for applying to Example', 'received_at': '2026-10-29T16:35:00+00:00',
        'body_text': "Thanks for applying to Example AI. We've received your application, and we are reviewing it carefully."}


class ReceiptReconciliationTests(unittest.TestCase):
    def test_unique_submitted_company_receipt_needs_no_model_or_invented_role(self):
        fetch = Mock(return_value={'messages': [MAIL], 'next_before_uid': None, 'has_more': False})
        with patch('services.mail_workflow.match_mail_for_job', side_effect=AssertionError('Must not spend AI quota')):
            page = run_mail_workflow(APP, 'fixture', 'secret', fetcher=fetch)
        match = page['matches'][0]
        self.assertIsNone(match['job_title']); self.assertIsNone(match['job_id'])
        self.assertEqual(match['message_uid'], 42)
        self.assertTrue(receipt_matches_job(APP, match))
        self.assertNotIn('secret', repr(page))

    def test_company_fallback_is_not_available_without_trusted_worker_context(self):
        for changes in [{'receipt_company_candidates': 0}, {'receipt_company_candidates': 2},
                        {'receipt_company_candidates': None}, {'submitted_at': None}]:
            self.assertEqual(explicit_receipts(APP | changes, [MAIL]), [])

    def test_sender_must_match_first_party_posting_domain(self):
        for sender in ['no-reply@evil.example', 'no-reply@example.ai.evil.test', 'example.ai <no-reply@evil.test>']:
            self.assertEqual(explicit_receipts(APP, [MAIL | {'sender': sender}]), [])
        for url in ['https://job-boards.greenhouse.io/example/jobs/1', 'https://www.linkedin.com/jobs/view/1']:
            self.assertEqual(explicit_receipts(APP | {'application_url': url}, [MAIL]), [])

    def test_missing_invalid_or_outside_submission_window_date_is_rejected(self):
        for stamp in [None, 'bad', '2026-10-29T16:35:00', '2026-10-28T16:35:00+00:00', '2026-11-10T16:35:00+00:00']:
            self.assertEqual(explicit_receipts(APP, [MAIL | {'received_at': stamp}]), [])

    def test_generic_or_negative_language_is_never_a_receipt(self):
        for body in ['Please apply to Example AI.', 'Example AI has not received your application.', 'Your application is not received by Example AI.']:
            self.assertEqual(explicit_receipts(APP, [MAIL | {'subject': 'Job update', 'body_text': body}]), [])

    def test_an_explicit_other_role_or_job_id_cannot_use_company_fallback(self):
        match = explicit_receipts(APP, [MAIL])[0]
        self.assertFalse(receipt_matches_job(APP, match | {'job_title': 'Other role'}))
        self.assertFalse(receipt_matches_job(APP, match | {'job_id': 'OTHER'}))

    def test_ambiguous_company_receipt_keeps_model_failure_and_cursor(self):
        fetch = Mock(return_value={'messages': [MAIL], 'next_before_uid': 42, 'has_more': True})
        from services.groq_client import AIError
        with patch('services.mail_workflow.match_mail_for_job', side_effect=AIError('Quota', 429)):
            with self.assertRaises(AIError): run_mail_workflow(APP | {'receipt_company_candidates': 2}, 'a', 'b', fetcher=fetch)

    def test_other_lifecycle_suggestions_keep_the_existing_model_path(self):
        fetch = Mock(return_value={'messages': [MAIL], 'next_before_uid': None, 'has_more': False})
        with patch('services.mail_workflow.match_mail_for_job', return_value=[]) as model:
            run_mail_workflow(APP | {'status': 'applied'}, 'a', 'b', fetcher=fetch)
        model.assert_called_once()
