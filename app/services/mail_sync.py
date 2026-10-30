"""Durable database schedule, fenced account leases and encrypted Gmail credentials.

Only the backend knows both the worker capability and encryption key. Browser
requests use normal owner-scoped RLS for metadata, never credential reads.
"""
import hashlib
import logging
from datetime import datetime, timezone
from threading import Event

from cryptography.fernet import Fernet, InvalidToken
from core.config import get_settings
from core.supabase import auth_client
from services.groq_client import AIError
from services.imap_client import ImapError
from services.mail_workflow import run_mail_workflow
from services.mail_receipts import receipt_matches_job

logger = logging.getLogger(__name__)


def configured():
    s = get_settings()
    return bool(s.mail_worker_key.get_secret_value() and s.mail_encryption_key.get_secret_value())


def cipher():
    key = get_settings().mail_encryption_key.get_secret_value()
    if not key:
        raise AIError('Persistent email sync is not configured on this server.', 503)
    return Fernet(key.encode())


def encrypt_password(user_id, email_address, password):
    # Bind ciphertext to owner and mailbox, preventing record substitution.
    import json
    return cipher().encrypt(json.dumps([user_id, email_address, password]).encode()).decode()


def decrypt_password(user_id, email_address, ciphertext):
    import json
    owner, address, password = json.loads(cipher().decrypt(ciphertext.encode()))
    if owner != user_id or address != email_address:
        raise InvalidToken()
    return password


def worker(action, **payload):
    key = get_settings().mail_worker_key.get_secret_value()
    if not key:
        raise AIError('Persistent email sync is not configured on this server.', 503)
    return auth_client().rpc('mail_worker', {'p_key': key, 'p_action': action, 'p_payload': payload}).execute().data


def safe_error(exc):
    if isinstance(exc, InvalidToken):
        return 'credential_unavailable'
    if isinstance(exc, (ImapError, OSError)):
        return 'gmail_unavailable'
    if isinstance(exc, AIError):
        return 'ai_rate_limit' if exc.status_code == 429 else 'ai_unavailable'
    return 'sync_failed'


def log_match(job, match):
    identity = match.get('message_id') or f"uid:{match['message_uid']}"
    return {k: match.get(k) for k in ('category', 'confidence', 'suggested_status', 'message_uid', 'message_id', 'evidence', 'sender', 'subject', 'interview_details', 'deadline_text')} | {
        'message_key': hashlib.sha256(identity.encode()).hexdigest(),
        'receipt_valid': receipt_matches_job(job, match),
    }


def sync_account(user_id, trigger='manual', application_id=None, claim=None):
    from services.ai_context import ai_scope
    with ai_scope(user_id,background=trigger!='manual'):
        return _sync_account(user_id,trigger,application_id,claim)


def _sync_account(user_id, trigger='manual', application_id=None, claim=None):
    try:
        claim = claim or worker('claim', user_id=user_id, trigger=trigger, application_id=application_id)
    except Exception:
        logger.warning('Mail sync could not acquire its database lease')
        return {'state': 'failed', 'error_code': 'sync_failed'}
    if not claim or claim['state'] != 'claimed':
        return claim or {'state': 'disconnected'}
    run_id = claim['run_id']; failures = []; partial = False; updated = []; pages = []
    try:
        password = decrypt_password(user_id, claim['email_address'], claim['ciphertext'])
        jobs = worker('jobs', user_id=user_id, trigger=trigger, application_id=application_id)
        partial |= len(jobs) > 20
        for job in jobs[:20]:
            error = None; matches = []; cursor = job.get('mail_before_uid'); page = None
            try:
                # Three bounded pages per job; persist cursor for the next sync.
                for _ in range(3):
                    page = run_mail_workflow(job, claim['email_address'], password, before_uid=cursor, limit=5)
                    matches.extend(page['matches'])
                    cursor = page.get('next_before_uid') if page.get('has_more') else None
                    if not cursor or any(receipt_matches_job(job, m) for m in matches):
                        break
                partial |= bool(cursor)
            except Exception as exc:
                error = safe_error(exc); failures.append(error)
            updated.append(worker('record', user_id=user_id, run_id=run_id, application_id=job['id'],
                                  matches=[log_match(job, m) for m in matches], before_uid=cursor, error_code=error,
                                  expected_company=job.get('company'), expected_title=job.get('job_title'), expected_job_id=job.get('job_id')))
            if page:
                pages.append(page | {'application_id': job['id']})
            # Free-tier rate limit: don't keep making model calls for every job.
            if error == 'ai_rate_limit':
                break
        status = 'partial' if (partial or (failures and len(updated) > len(failures))) else 'failed' if failures else 'success'
        worker('finish', user_id=user_id, run_id=run_id, status=status,
               error_code=failures[0] if failures else 'more_candidates' if partial else None, trigger=trigger)
        return {'state': 'completed', 'run_id': run_id, 'status': status, 'applications': updated, 'pages': pages}
    except Exception as exc:
        # Never persist exception text (SDK errors can carry request material).
        try:
            worker('finish', user_id=user_id, run_id=run_id, status='failed', error_code=safe_error(exc), trigger=trigger)
        except Exception:
            logger.warning('Mail sync finish could not be recorded; its lease will expire')
        return {'state': 'completed', 'run_id': run_id, 'status': 'failed', 'applications': [], 'pages': []}


def scheduler_loop(stop: Event):
    """One bounded worker; DB leases also fence concurrent API/server instances."""
    last_cleanup = None
    while not stop.is_set():
        try:
            if configured():
                today = datetime.now(timezone.utc).date()
                if today != last_cleanup:
                    worker('cleanup'); last_cleanup = today
                for user_id in worker('due'):
                    if stop.is_set():
                        break
                    sync_account(user_id, 'scheduled')
        except Exception:
            logger.warning('Mail scheduler tick failed; retrying on next tick')
        stop.wait(60)
