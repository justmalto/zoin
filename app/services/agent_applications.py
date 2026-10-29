"""Threshold-gated submission journal linked to normal Zoin application tracking."""
import hashlib
import json
from datetime import datetime, timezone
from threading import BoundedSemaphore
from uuid import uuid4
from fastapi import HTTPException
from services.discovery_store import database
from services.job_discovery import canonical

SUBMISSION_SLOT=BoundedSemaphore(1)


def setup(db):
    db.execute('''create table if not exists agent_attempts(
      id text primary key, owner text not null, job_url text not null,
      application_id text, state text not null, note text not null,
      created_at text not null, updated_at text not null, receipt_reference text,
      unique(owner,job_url))''')
    if 'run_id' not in {r[1] for r in db.execute('pragma table_info(agent_attempts)')}:db.execute('alter table agent_attempts add column run_id text')


def timestamp():return datetime.now(timezone.utc).isoformat()


def attempts(owner):
    with database() as db:
        setup(db)
        return [dict(row) for row in db.execute('select id,job_url,application_id,state,note,created_at,updated_at,receipt_reference from agent_attempts where owner=? order by created_at desc',(owner,))]


def update(owner,identifier,state,note,*,application_id=None,receipt=None):
    with database() as db:
        setup(db)
        db.execute('update agent_attempts set state=?,note=?,updated_at=?,application_id=coalesce(?,application_id),receipt_reference=coalesce(?,receipt_reference) where owner=? and id=?',
                   (state,note,timestamp(),application_id,receipt,owner,identifier))


def job_identity(url):
    value=canonical(url)
    if value:return value
    from core.config import get_settings
    origin=get_settings().discovery_mock_origin
    return url if origin and url==origin.rstrip('/')+'/apply' else None


def find_recommendation(owner,run_id,url):
    with database() as db:
        row=db.execute("select result from discovery_runs where id=? and owner=? and status='complete'",(run_id,owner)).fetchone()
        account=db.execute('select snapshot from discovery_accounts where owner=?',(owner,)).fetchone()
    if not row or not account:raise HTTPException(404,'Recommendation not found.')
    key=job_identity(url)
    if not key:raise HTTPException(404,'Recommendation not found.')
    job=next((j for j in json.loads(row['result']).get('jobs',[]) if job_identity(j['url'])==key),None)
    if not job:raise HTTPException(404,'Recommendation not found.')
    return job,json.loads(account['snapshot'])['preferences']


def submit(owner,run_id,url,client,applicant,*,adapter=None,tailored=None):
    job,prefs=find_recommendation(owner,run_id,url)
    if prefs.get('paused'):raise HTTPException(409,'Automation is paused.')
    assessment=(tailored or {}).get('assessment') or job.get('assessment') or {}
    if assessment.get('method')!='profession_independent_requirement_coverage' or assessment.get('score',0)<prefs.get('matching_threshold',80) or assessment.get('mandatory_blockers') or not assessment.get('eligible_for_automation'):
        raise HTTPException(409,'This job does not pass the current threshold and mandatory requirement checks.')
    if (tailored is None and prefs.get('automation_level','recommend')!='existing_resume') or (tailored is not None and prefs.get('automation_level') not in ('review_resume','tailored_resume')):
        raise HTTPException(409,'Enable existing-resume automation first. Tailored documents require separate review and reassessment.')
    if adapter is None or not adapter.supports(job):
        raise HTTPException(409,'This employer has no enabled submission adapter. Open the posting and apply manually.')
    if callable(getattr(type(adapter),'requires_real_consent',None)) and adapter.requires_real_consent(job) and not prefs.get('real_applications_enabled',False):
        raise HTTPException(409,'Real-site submission is turned off. Enable applications on real job sites in Agent settings. You can still inspect the link without submitting.')
    if hasattr(adapter,'preflight'):adapter.preflight()
    rows=client.table('resumes').select('id,file_path,file_name,mime_type,resume_text').eq('id',job['resume_id']).eq('user_id',owner).execute().data
    if not rows:raise HTTPException(409,'The qualifying resume is no longer available. Search and assess again.')
    resume=rows[0]
    if hashlib.sha256(resume['resume_text'][:18000].encode()).hexdigest()!=job['resume_hash']:
        raise HTTPException(409,'The resume changed after assessment. Search and assess again.')
    if not SUBMISSION_SLOT.acquire(blocking=False):raise HTTPException(409,'Another local submission is running. Retry shortly.')
    identifier=str(uuid4())
    try:
        # Remote question reads must not hold the local reservation write lock.
        with database() as db:
            setup(db)
            pending=db.execute('select application_id,state from agent_attempts where owner=? and job_url=?',(owner,job_identity(url))).fetchone()
        ready=False
        if pending and pending['state']=='needs_answers':
            from services.application_questions import read
            ready=read(client,owner,pending['application_id'])['state']=='ready'
        with database() as db:
            setup(db);db.commit();db.execute('begin immediate')
            old=db.execute('select id,state,application_id,note from agent_attempts where owner=? and job_url=?',(owner,job_identity(url))).fetchone()
            if old and old['state'] not in ('needs_answers','retry_ready'):return dict(old)|{'already_recorded':True}
            if old and old['state']=='needs_answers' and not ready:return dict(old)|{'already_recorded':True}
            if old:identifier=old['id']
            from services.automation import usage
            if usage(owner)>=prefs.get('daily_application_limit',5):raise HTTPException(409,'Daily application limit reached. Remaining jobs wait until the next day (Asia/Kolkata).')
            db.execute('insert or ignore into agent_attempts(id,owner,job_url,state,note,created_at,updated_at) values(?,?,?,?,?,?,?)',
                       (identifier,owner,job_identity(url),'recording','Preparing the tracked application before submission.',timestamp(),timestamp()))
            db.execute("update agent_attempts set state='recording',run_id=?,created_at=? where owner=? and id=?",(run_id,timestamp(),owner,identifier))
        try:
            owned=client.table('applications').select('id,status,job_description,application_url,submitted_at,receipt_confirmed_at').eq('user_id',owner).execute().data
            existing=[r for r in owned if r.get('application_url') and job_identity(r['application_url'])==job_identity(job['url'])]
            existing.sort(key=lambda r:bool(r.get('submitted_at') or r.get('receipt_confirmed_at') or r['status']!='found'),reverse=True)
            if existing:
                application=existing[0]
                if application.get('job_description') and application['job_description']!=job['description']:
                    update(owner,identifier,'needs_user_action','The tracked job description changed after assessment. Reassess it before submitting.',application_id=application['id'])
                    return next(a for a in attempts(owner) if a['id']==identifier)
                if application['status']!='found' or application.get('submitted_at') or application.get('receipt_confirmed_at'):
                    update(owner,identifier,'needs_user_action','An application already exists beyond Found; no submission was attempted.',application_id=application['id'])
                    return next(a for a in attempts(owner) if a['id']==identifier)
            else:
                from schemas.application import ApplicationCreate
                payload=ApplicationCreate(company=job['company'],job_title=job['title'],job_description=job['description'],job_id=job.get('id'),application_url=job['url'],location=job.get('location'),selected_resume_id=resume['id'])
                result=client.rpc('create_application',{'p_payload':payload.model_dump(mode='json')}).execute().data
                application=result[0] if isinstance(result,list) else result
            update(owner,identifier,'prepared','Tracked as Found. Submission is not yet confirmed.',application_id=str(application['id']))
            form_answers=None
            if callable(getattr(type(adapter),'questions',None)):
                from services.application_questions import register,read
                fields=adapter.questions(job)
                if fields:
                    current=register(client,owner,application|{'company':job['company'],'application_url':job['url'],'job_description':job['description']},fields,identity=applicant)
                    if current['state']!='ready':
                        update(owner,identifier,'needs_answers','Answer the employer’s questions in this job to continue.',application_id=str(application['id']))
                        return next(a for a in attempts(owner) if a['id']==identifier)
                    form_answers={q['key']:q.get('answer','') for q in current['questions']}
            from core.config import get_settings
            if tailored:
                from services.resume_tailoring import render_docx
                if tailored['source_hash']!=job['resume_hash']:raise ValueError('Source changed')
                content=render_docx(tailored['text'])
                resume=resume|{'file_name':tailored['file_name'],'mime_type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
            else:content=client.storage.from_(get_settings().supabase_resume_bucket).download(resume['file_path'])
            if not isinstance(content,bytes) or len(content)>10*1024*1024:raise ValueError('Invalid resume bytes')
        except Exception as failure:
            from services.browser_applications import FormBlocked
            if isinstance(failure,FormBlocked):
                update(owner,identifier,'blocked_before_submission',failure.code+': '+failure.message,application_id=str(application['id']) if 'application' in locals() else None)
                return next(a for a in attempts(owner) if a['id']==identifier)
            update(owner,identifier,'needs_reconciliation','Application preparation did not complete. Verify the tracked record before retrying.')
            return next(a for a in attempts(owner) if a['id']==identifier)
        update(owner,identifier,'submitting','Submission started. Do not retry while its outcome is unknown.')
        try:
            document={'name':resume['file_name'],'mime_type':resume['mime_type'],'bytes':content}
            outcome=adapter.submit(job,applicant,document,answers=form_answers) if form_answers is not None else adapter.submit(job,applicant,document)
            if outcome.get('state') not in ('submitted','needs_user_action'):raise ValueError('Ambiguous result')
            update(owner,identifier,outcome['state'],
                   'Submitted by agent; awaiting an application-received email. Zoin remains Found until confirmation.' if outcome['state']=='submitted' else 'The form needs your input. No confirmed submission.',
                   receipt=str(outcome.get('reference',''))[:200] or None)
            if outcome['state']=='submitted':
                # A successful form receipt is recorded separately from Gmail-confirmed Applied.
                try:client.table('applications').update({'submitted_at':timestamp()}).eq('id',str(application['id'])).eq('user_id',owner).execute()
                except Exception:pass  # The durable local receipt still records the actual outcome.
        except Exception as failure:
            from services.browser_applications import FormBlocked
            if isinstance(failure,FormBlocked):
                update(owner,identifier,'blocked_before_submission',failure.code+': '+failure.message)
                return next(a for a in attempts(owner) if a['id']==identifier)
            update(owner,identifier,'outcome_unknown','The submission outcome is unknown. Verify the employer site or inbox; automatic retry is blocked.')
        return next(a for a in attempts(owner) if a['id']==identifier)
    finally:SUBMISSION_SLOT.release()


def retry_blocked(owner,application_id):
    """Explicit retry only when the adapter guarantees no final submission began."""
    if not SUBMISSION_SLOT.acquire(blocking=False):raise HTTPException(409,'Another application is running. Retry shortly.')
    try:
        with database() as db:
            setup(db)
            row=db.execute('select * from agent_attempts where owner=? and application_id=? order by created_at desc limit 1',(owner,str(application_id))).fetchone()
            if not row or row['state']!='blocked_before_submission':raise HTTPException(409,'Only an attempt blocked before final submission can be retried. Reconcile unknown outcomes manually.')
            db.execute("update agent_attempts set state='retry_ready',note='Retry requested; all checks still apply.',updated_at=? where id=? and owner=?",(timestamp(),row['id'],owner))
        return {'state':'retry_ready'}
    finally:SUBMISSION_SLOT.release()
