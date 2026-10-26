"""Automatic personal tracker updates only for explicit, identity-matched receipts."""
import re
from services.mail_workflow import normalized

def receipt_matches_job(application,match):
    if match.get('category')!='confirmation' or match.get('suggested_status')!='applied' or match.get('confidence',0)<0.9 or not match.get('evidence'):
        return False
    evidence=normalized(' '.join(match['evidence']))
    if re.search(r'\b(?:not|never) received\b|\bapplication (?:was |is )?not\b',evidence):return False
    receipt_language=(re.search(r'\bapplication\b.{0,100}\b(received|submitted|acknowledged)\b',evidence) or
                      re.search(r'\b(received|acknowledge)\b.{0,100}\bapplication\b',evidence) or
                      re.search(r'\bthank(?:s| you) for (?:applying|your application)\b|\bsuccessfully applied\b',evidence))
    if not receipt_language:return False
    expected=normalized(application.get('job_id') or '')
    actual=normalized(match.get('job_id') or '')
    if expected and actual:
        return expected==actual
    return (bool(match.get('company')) and bool(match.get('job_title')) and
            normalized(match['company'])==normalized(application.get('company') or '') and
            normalized(match['job_title'])==normalized(application.get('job_title') or ''))

def apply_received_mail(database,application,matches):
    updates=0; current=application
    for match in matches:
        if not receipt_matches_job(application,match):continue
        value=database.rpc('apply_application_receipt',{'p_application_id':application['id'],
            'p_uid':match['message_uid'],'p_message_id':match.get('message_id',''),'p_evidence':match['evidence']}).execute().data
        if isinstance(value,list):value=value[0]
        updates+=int(value['updated']);current=value['application']
        if current.get('receipt_confirmed_at'):break
    return {'status_updates_applied':updates,'application':current}
