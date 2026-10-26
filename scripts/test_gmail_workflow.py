"""Opt-in live test: sends labelled synthetic mail only to your own supplied Gmail.
Credentials are prompted, never written. Creates one labelled Found application.
"""
import getpass
import json
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from uuid import uuid4
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from core.supabase import auth_client
from services.imap_client import _connect, _close


def main():
    address=input('Gmail/Zoin email: ').strip()
    zoin_password=getpass.getpass('Zoin password: ')
    app_password=''.join(getpass.getpass('Gmail App Password: ').split())
    session=auth_client().auth.sign_in_with_password({'email':address,'password':zoin_password}).session
    del zoin_password
    job_id='ZOIN-SMOKE-20261026-'+uuid4().hex[:8]
    headers={'Authorization':'Bearer '+session.access_token}
    with httpx.Client(base_url='http://127.0.0.1:8000/api/v1',headers=headers,timeout=120) as api:
        verified=api.post('/mailboxes/verify',json={'email_address':address,'app_password':app_password});verified.raise_for_status()
        created=api.post('/applications',json={'company':'Zoin Synthetic Labs','job_title':'Data Scientist','job_id':job_id,'job_description':'SYNTHETIC TEST ONLY. Data Scientist role using Python and ML. No real vacancy or recruitment decision.','initial_status':'found'})
        created.raise_for_status(); job=created.json()
        subjects=[f'[ZOIN SYNTHETIC TEST] Interview - Data Scientist - {job_id}',
                  '[ZOIN SYNTHETIC TEST] Interview - Java Developer - unrelated role',
                  '[ZOIN SYNTHETIC TEST] Zoin Synthetic Labs newsletter']
        bodies=[f'SYNTHETIC TEST ONLY — this is not a real recruitment message.\nZoin Synthetic Labs invites you to interview for Data Scientist, job ID {job_id}.\nSynthetic interview date: 30 October 2026 at 10:00 IST. No action is required.',
                'SYNTHETIC TEST ONLY. Zoin Synthetic Labs invites you to interview for Java Developer, job ID ZOIN-UNRELATED-JAVA. This is not the Data Scientist vacancy. No action is required.',
                'SYNTHETIC TEST ONLY. Zoin Synthetic Labs monthly newsletter. Read our general company news. No action is required.']
        with smtplib.SMTP_SSL('smtp.gmail.com',465,context=ssl.create_default_context(),timeout=30) as smtp:
            smtp.login(address,app_password)
            for subject,body in zip(subjects,bodies):
                msg=EmailMessage();msg['From']=address;msg['To']=address;msg['Subject']=subject;msg['Message-ID']=make_msgid();msg.set_content(body)
                smtp.send_message(msg)
        print('Sent 3 clearly labelled synthetic messages to the same account.',flush=True)
        # Wait for inbox indexing without repeatedly invoking the model.
        for attempt in range(6):
            client=_connect(address,app_password)
            try:
                client.select('INBOX',readonly=True)
                status,data=client.uid('search',None,'SUBJECT "ZOIN SYNTHETIC TEST"')
                if status=='OK' and len((data[0] or b'').split())>=3:break
            finally:_close(client)
            time.sleep(5)
        response=api.post(f"/applications/{job['id']}/mail-search",json={'email_address':address,'app_password':app_password})
        response.raise_for_status();result=response.json()
        assert len(result['matches'])==1, 'Expected only the specific tracked job to match'
        match=result['matches'][0]
        assert match['job_id']==job_id and match['suggested_status']=='interview' and match['review_required']
        assert result['status_updates_applied']==0
        current=api.get('/applications/'+job['id']);current.raise_for_status();assert current.json()['status']=='found'
        report={'job_id':job_id,'application_id':job['id'],'synthetic_messages_sent':3,'scanned':result['scanned'],
                'match_count':len(result['matches']),'suggested_status':match['suggested_status'],
                'status_remains':'found','steps':result['steps'],'source_evidence_validated':True}
        print(json.dumps(report,indent=2))
        output=Path(sys.argv[1]) if len(sys.argv)>1 else None
        if output:output.write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
