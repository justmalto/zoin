from uuid import UUID

from fastapi import APIRouter, Request, File, Form, UploadFile

from core.auth import CurrentUser
from core.exceptions import not_found
from core.supabase import user_client
from schemas.ai import JobExtractRequest, ResumeMatchRequest, MailSearchRequest
from services.mail_workflow import run_mail_workflow
from services.groq_client import AIError
from services.imap_client import ImapError
from services.job_extractor import extract_job
from services.recommendation_service import match_resume
from services.resume_upload import read_resume
from services.mail_receipts import apply_received_mail

router = APIRouter(tags=["analysis"])


@router.post('/analysis/job')
def extract_job_details(payload: JobExtractRequest, user_id: CurrentUser):
    """Extract from pasted text; never fetch an arbitrary client URL."""
    return extract_job(payload.text)


@router.post('/analysis/resume-match')
def match_saved_resume(payload: ResumeMatchRequest, request: Request, user_id: CurrentUser):
    result = (user_client(request.state.access_token).table('resumes').select('id,resume_text')
              .eq('id', str(payload.resume_id)).eq('user_id', user_id).execute())
    if not result.data:
        raise not_found('Resume')
    return match_resume(payload.job_description, result.data[0]['resume_text']) | {'resume_id': str(payload.resume_id)}


@router.post('/analysis/resume-preview')
def preview_resume(user_id:CurrentUser, file:UploadFile=File(...), job_description:str=Form(...,min_length=20,max_length=18000)):
    """No database or Storage writes: request-scoped bytes/text only."""
    try:
        _,text=read_resume(file)
    finally:
        file.file.close()
    return match_resume(job_description,text) | {'temporary':True,'resume_file_name':file.filename}


@router.post('/applications/{application_id}/mail-search')
def search_tracked_job_mail(application_id: UUID, payload: MailSearchRequest, request: Request, user_id: CurrentUser):
    """Legacy read-only search is retained for compatibility; use /mail-sync/now for persisted sync."""
    result = (user_client(request.state.access_token).table('applications').select('*')
              .eq('id', str(application_id)).eq('user_id', user_id).execute())
    if not result.data:
        raise not_found('Application')
    password = ''.join(payload.app_password.get_secret_value().split())
    if len(password) != 16:
        raise AIError('Gmail App Password must contain 16 characters', 422)
    try:
        result_page = run_mail_workflow(result.data[0], str(payload.email_address), password,
                                        before_uid=payload.before_uid, after_uid=payload.after_uid, limit=payload.limit)
    except (ImapError, OSError) as exc:
        raise AIError('Unable to read Gmail; verify the mailbox and App Password', 502) from exc
    database=user_client(request.state.access_token)
    receipt=apply_received_mail(database,result.data[0],result_page['matches'])
    return result_page | receipt | {'application_id':str(application_id),
                          'scope':'Legacy on-demand search. Use Sync now for persistent sync history.'}
