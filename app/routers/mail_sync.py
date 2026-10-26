from uuid import UUID
from datetime import datetime, timezone
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from core.auth import CurrentUser
from core.supabase import user_client
from routers.mailboxes import MailboxVerify, verify_mailbox
from services.mail_sync import worker, encrypt_password, sync_account, configured

router = APIRouter(prefix='/mail-sync', tags=['mail-sync'])


class SyncSettings(BaseModel):
    interval_days: int = Field(ge=1, le=7)
    enabled: bool


class SyncRequest(BaseModel):
    application_id: UUID | None = None


@router.get('')
def state(request: Request, user_id: CurrentUser):
    db = user_client(request.state.access_token)
    settings = db.table('mail_sync_settings').select('*').eq('user_id', user_id).execute().data
    runs = db.table('mail_sync_runs').select('*').eq('user_id', user_id).order('started_at', desc=True).limit(10).execute().data
    connection = settings[0] if settings else None
    if connection:
        lease = connection.get('lease_until')
        connection['syncing'] = bool(connection.get('active_run_id') and lease and datetime.fromisoformat(lease.replace('Z','+00:00')) > datetime.now(timezone.utc))
    return {'configured': configured(), 'connection': connection, 'runs': runs}


@router.post('/connect')
def connect(payload: MailboxVerify, request: Request, user_id: CurrentUser):
    verify_mailbox(payload, user_id)
    address = str(payload.email_address)
    password = ''.join(payload.app_password.get_secret_value().split())
    worker('connect', user_id=user_id, email_address=address,
           ciphertext=encrypt_password(user_id, address, password))
    return {'email_address': address, 'persistence': 'encrypted-server-storage'}


@router.delete('/connection')
def disconnect(user_id: CurrentUser):
    worker('disconnect', user_id=user_id)
    return {'disconnected': True}


@router.patch('/settings')
def settings(payload: SyncSettings, user_id: CurrentUser):
    worker('settings', user_id=user_id, **payload.model_dump())
    return {'saved': True}


@router.post('/now', status_code=202)
def now(payload: SyncRequest, request: Request, tasks: BackgroundTasks, user_id: CurrentUser):
    db = user_client(request.state.access_token)
    if payload.application_id:
        rows = db.table('applications').select('id,status').eq('id', str(payload.application_id)).eq('user_id', user_id).execute().data
        if not rows:
            raise HTTPException(404, 'Job not found')
        if rows[0]['status'] not in ('found', 'applied'):
            raise HTTPException(409, 'Email sync is limited to Found confirmation checks and Applied jobs.')
    if not db.table('mail_sync_settings').select('user_id').eq('user_id', user_id).execute().data:
        raise HTTPException(409, 'Connect Gmail in settings first.')
    claim = worker('claim', user_id=user_id, trigger='manual', application_id=str(payload.application_id) if payload.application_id else None)
    if claim['state'] != 'claimed':
        raise HTTPException(409, 'A sync is already running. Check the sync log.')
    tasks.add_task(sync_account, user_id, 'manual', str(payload.application_id) if payload.application_id else None, claim)
    return {'state': 'queued', 'run_id': claim['run_id']}


@router.get('/messages/{application_id}')
def messages(application_id: UUID, request: Request, user_id: CurrentUser):
    return user_client(request.state.access_token).table('mail_sync_messages').select('*').eq('user_id', user_id).eq('application_id', str(application_id)).order('processed_at', desc=True).limit(30).execute().data
