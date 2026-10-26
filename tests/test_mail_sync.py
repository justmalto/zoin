import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.fernet import Fernet, InvalidToken
from services import mail_sync

class MailSyncTests(unittest.TestCase):
    def test_ciphertext_is_bound_to_owner_and_address(self):
        with patch.object(mail_sync, 'cipher', return_value=Fernet(Fernet.generate_key())):
            encrypted=mail_sync.encrypt_password('a','a@example.invalid','abcdefghijklmnop')
            self.assertNotIn('abcdefghijklmnop',encrypted)
            self.assertEqual(mail_sync.decrypt_password('a','a@example.invalid',encrypted),'abcdefghijklmnop')
            with self.assertRaises(InvalidToken):mail_sync.decrypt_password('b','a@example.invalid',encrypted)
            with self.assertRaises(InvalidToken):mail_sync.decrypt_password('a','b@example.invalid',encrypted)
    def test_busy_account_does_not_read_mail(self):
        with patch.object(mail_sync,'worker',return_value={'state':'busy'}),patch.object(mail_sync,'run_mail_workflow') as read:
            self.assertEqual(mail_sync.sync_account('a')['state'],'busy');read.assert_not_called()
    def test_success_records_then_finishes(self):
        calls=[]
        def worker(action,**data):
            calls.append((action,data))
            if action=='claim':return {'state':'claimed','run_id':'r','email_address':'a@example.invalid','ciphertext':'encrypted'}
            if action=='jobs':return [{'id':'j','status':'found','company':'Example','job_title':'Engineer'}]
            return {}
        with patch.object(mail_sync,'worker',side_effect=worker),patch.object(mail_sync,'decrypt_password',return_value='secret'),patch.object(mail_sync,'run_mail_workflow',return_value={'matches':[],'has_more':False}):
            self.assertEqual(mail_sync.sync_account('a')['status'],'success')
        self.assertEqual([x[0] for x in calls],['claim','jobs','record','finish'])
        self.assertIsNone(calls[-2][1]['error_code'])
        self.assertNotIn('secret',repr(calls))
    def test_failure_logs_only_safe_code(self):
        calls=[]
        def worker(action,**data):
            calls.append((action,data))
            if action=='claim':return {'state':'claimed','run_id':'r','email_address':'a@example.invalid','ciphertext':'encrypted'}
            if action=='jobs':return [{'id':'j'}]
            return {}
        with patch.object(mail_sync,'worker',side_effect=worker),patch.object(mail_sync,'decrypt_password',return_value='secret'),patch.object(mail_sync,'run_mail_workflow',side_effect=OSError('private email contents')):
            self.assertEqual(mail_sync.sync_account('a')['status'],'failed')
        self.assertNotIn('private email contents',repr(calls));self.assertEqual(calls[-1][1]['error_code'],'gmail_unavailable')
    def test_cursor_is_preserved_for_bounded_continuation(self):
        calls=[]
        def worker(action,**data):
            calls.append((action,data))
            if action=='claim':return {'state':'claimed','run_id':'r','email_address':'a@example.invalid','ciphertext':'x'}
            if action=='jobs':return [{'id':'j'}]
            return {}
        with patch.object(mail_sync,'worker',side_effect=worker),patch.object(mail_sync,'decrypt_password',return_value='secret'),patch.object(mail_sync,'run_mail_workflow',return_value={'matches':[],'has_more':True,'next_before_uid':42}) as read:
            self.assertEqual(mail_sync.sync_account('a')['status'],'partial');self.assertEqual(read.call_count,3)
        self.assertEqual(calls[-2][1]['before_uid'],42)
    def test_logged_identity_is_hashed_and_receipt_requires_source_language(self):
        job={'company':'Example','job_title':'Engineer'}
        match={'message_uid':42,'message_id':'<fixture>','category':'confirmation','suggested_status':'applied','confidence':1,'company':'Example','job_title':'Engineer','evidence':['Your application was received.']}
        result=mail_sync.log_match(job,match)
        self.assertTrue(result['receipt_valid']);self.assertEqual(len(result['message_key']),64)
        match['evidence']=['You were invited to apply.'];self.assertFalse(mail_sync.log_match(job,match)['receipt_valid'])
if __name__=='__main__':unittest.main()
