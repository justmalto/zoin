"""Automatic personal tracker updates only for explicit, identity-matched receipts."""
import re
from datetime import datetime, timedelta
from email.utils import parseaddr
from urllib.parse import urlsplit
from services.mail_workflow import normalized

def company_receipt_scope(application, match):
    """Only worker-supplied ownership/ambiguity context can enable role-less receipts."""
    if application.get('receipt_company_candidates') != 1 or not application.get('submitted_at'):
        return False
    if match.get('job_id') or match.get('job_title'):
        return False  # An explicitly different role/ID cannot use the generic fallback.
    try:
        host = (urlsplit(application.get('application_url') or '').hostname or '').casefold()
    except ValueError:
        return False
    host = re.sub(r'^(?:www|careers|jobs|apply)\.', '', host)
    sender = parseaddr(match.get('sender') or '')[1].rpartition('@')[2].casefold()
    if not host or host in ('greenhouse.io', 'lever.co', 'linkedin.com', 'peoplestrong.com') or any(host.endswith('.' + shared) for shared in ('greenhouse.io', 'lever.co', 'linkedin.com', 'peoplestrong.com')):
        return False
    if sender != host:
        return False
    if not isinstance(match.get('received_at'), str):
        return False
    try:
        submitted = datetime.fromisoformat(application['submitted_at'].replace('Z', '+00:00'))
        received = datetime.fromisoformat(match['received_at'].replace('Z', '+00:00'))
        if submitted.tzinfo is None or received.tzinfo is None:
            return False
        return submitted - timedelta(minutes=15) <= received <= submitted + timedelta(days=7)
    except (KeyError, TypeError, ValueError):
        return False

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
    company_matches = bool(match.get('company')) and normalized(match['company']) == normalized(application.get('company') or '')
    return company_matches and ((bool(match.get('job_title')) and
            normalized(match['job_title']) == normalized(application.get('job_title') or '')) or company_receipt_scope(application, match))


def explicit_receipts(application, messages):
    """Extract literal, unambiguous receipts without spending an AI quota."""
    results = []
    for message in messages:
        text = '\n'.join(message.get(k, '') for k in ('subject', 'body_text'))
        def quote(value):
            if not value: return None
            found = re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text, re.I)
            return found.group() if found else None
        match = {'message_uid': message['uid'], 'message_id': message.get('message_id', ''),
            'sender': message.get('sender', ''), 'subject': message.get('subject', ''),
            'received_at': message.get('received_at'), 'category': 'confirmation',
            'suggested_status': 'applied', 'confidence': 1.0, 'company': quote(application.get('company')),
            'job_title': quote(application.get('job_title')), 'job_id': quote(application.get('job_id')),
            'evidence': [line.strip() for line in text.splitlines() if line.strip()],
            'reason': 'Explicit receipt with verified tracked-job identity.', 'review_required': True,
            'interview_details': None, 'deadline_text': None}
        if receipt_matches_job(application, match): results.append(match)
    return results

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
