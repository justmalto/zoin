"""Feature-gated discovery and application assistance; all data is owner scoped."""
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from core.auth import CurrentUser
from core.config import get_settings
from core.supabase import user_client
from schemas.discovery import DiscoveryPreferences
from schemas.career_agent import AgentApplicationRequest, ResumeDraftRequest
from services.job_discovery import profile_resumes
from services import discovery_store

router = APIRouter(prefix='/discovery', tags=['local discovery'])


def enabled():
    if not get_settings().discovery_enabled:
        raise HTTPException(404, 'Local discovery is not enabled on this backend.')


@router.get('')
def get_discovery(request: Request, user_id: CurrentUser):
    enabled()
    from services.automation import remember_session
    remember_session(user_id,request.state.access_token)
    return discovery_store.latest(user_id)


@router.post('/runs', status_code=202)
def start_discovery(payload: DiscoveryPreferences, request: Request, tasks: BackgroundTasks, user_id: CurrentUser):
    enabled()
    from services.automation import remember_session
    remember_session(user_id,request.state.access_token)
    prefs = payload.model_dump()
    prefs['locations'] = list(dict.fromkeys(x.strip()[:100] for x in prefs['locations'] if x.strip()))
    prefs['tracks'] = list(dict.fromkeys(prefs['tracks']))

    client = user_client(request.state.access_token)
    resumes = client.table('resumes').select('id,file_name,resume_text').eq('user_id', user_id).limit(10).execute().data
    if not resumes: raise HTTPException(422, 'Save at least one readable resume first.')
    applications = client.table('applications').select('application_url').eq('user_id', user_id).limit(1000).execute().data
    excluded = [a['application_url'] for a in applications if a.get('application_url')]
    snapshot = discovery_store.configure(user_id, [], prefs, excluded, resumes=resumes)
    run = discovery_store.enqueue(user_id, snapshot)
    tasks.add_task(discovery_store.process_one)
    return run


@router.delete('', status_code=204)
def delete_discovery(user_id: CurrentUser):
    enabled()
    discovery_store.erase(user_id)


@router.get('/agent-applications')
def agent_applications(user_id: CurrentUser):
    enabled()
    from services.agent_applications import attempts
    return attempts(user_id)


@router.post('/agent-applications')
def apply_recommendation(payload: AgentApplicationRequest, request: Request, tasks: BackgroundTasks, user_id: CurrentUser):
    enabled()
    from services.agent_applications import submit
    from services.browser_applications import application_browser
    settings=get_settings()
    adapter=application_browser()
    result=submit(user_id,payload.run_id,payload.job_url,user_client(request.state.access_token),
                  {'name':payload.applicant_name,'email':str(payload.applicant_email)},adapter=adapter)
    if result['state'] in ('submitted','outcome_unknown') and result.get('application_id'):
        from services.mail_sync import configured,sync_account
        if configured():tasks.add_task(sync_account,user_id,'job_saved',result['application_id'])
    return result


@router.post('/resume-draft')
def preview_resume_draft(payload: ResumeDraftRequest, request: Request, user_id: CurrentUser):
    enabled()
    from services.agent_applications import find_recommendation
    from services.resume_tailoring import propose
    from services import discovery_store
    job,_=find_recommendation(user_id,payload.run_id,payload.job_url)
    client=user_client(request.state.access_token)
    rows=client.table('resumes').select('id,resume_text').eq('id',job['resume_id']).eq('user_id',user_id).execute().data
    if not rows:raise HTTPException(404,'Qualifying resume not found.')
    import hashlib
    if hashlib.sha256(rows[0]['resume_text'][:18000].encode()).hexdigest()!=job['resume_hash']:
        raise HTTPException(409,'Resume changed. Reassess before tailoring.')
    cache=lambda action,key,value:discovery_store.ai_cache(user_id,action,key,value)
    return propose(rows[0]['resume_text'],job['description'],job.get('assessment',{}).get('suggestions',[]),cache=cache)


@router.post('/resume-draft/download')
def download_resume_draft(payload: ResumeDraftRequest, request: Request, user_id: CurrentUser):
    from fastapi import Response
    from services.resume_tailoring import render_docx
    draft=preview_resume_draft(payload,request,user_id)
    return Response(render_docx(draft['text']),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition':'attachment; filename="zoin-tailored-draft.docx"','Cache-Control':'no-store'})


@router.put('/preferences')
def save_preferences(payload: DiscoveryPreferences, request: Request, user_id: CurrentUser):
    enabled()
    from services.automation import remember_session
    remember_session(user_id,request.state.access_token)
    return discovery_store.save_preferences(user_id,payload.model_dump())


@router.get('/activity')
def automation_activity(request: Request, user_id: CurrentUser):
    enabled()
    from services.automation import activity
    return activity(user_id,user_client(request.state.access_token))


@router.post('/drafts')
def create_attached_draft(payload: ResumeDraftRequest, request: Request, user_id: CurrentUser):
    enabled()
    from services.automation import create_draft
    return create_draft(user_id,payload.run_id,payload.job_url,user_client(request.state.access_token))


@router.get('/drafts/{identifier}')
def get_attached_draft(identifier: str, user_id: CurrentUser):
    enabled()
    from services.automation import draft,public_draft
    return public_draft(draft(user_id,identifier),True)


@router.delete('/drafts/{identifier}',status_code=204)
def discard_attached_draft(identifier: str, user_id: CurrentUser):
    enabled()
    from services.automation import discard
    discard(user_id,identifier)


@router.post('/drafts/{identifier}/approve')
def approve_attached_draft(identifier: str, request: Request, tasks: BackgroundTasks, user_id: CurrentUser):
    enabled()
    from services.automation import submit_draft
    from services.browser_applications import application_browser
    settings=get_settings();client=user_client(request.state.access_token)
    person=client.auth.get_user(request.state.access_token).user
    if not person or str(person.id)!=user_id:raise HTTPException(401,'Sign in again before approving.')
    if not person.user_metadata.get('name'):raise HTTPException(422,'Set your applicant name before submitting.')
    adapter=application_browser()
    result=submit_draft(user_id,identifier,client,{'name':person.user_metadata.get('name') or 'Applicant','email':person.email},adapter,approve=True)
    if result['state'] in ('submitted','outcome_unknown') and result.get('application_id'):
        from services.mail_sync import configured,sync_account
        if configured():tasks.add_task(sync_account,user_id,'job_saved',result['application_id'])
    return result


@router.get('/drafts/{identifier}/download')
def download_attached_draft(identifier: str, user_id: CurrentUser):
    enabled()
    from fastapi import Response
    from services.automation import draft
    from services.discovery_vault import open_snapshot
    from services.resume_tailoring import render_docx
    row=draft(user_id,identifier)
    if row['state']=='discarded':raise HTTPException(410,'Draft was discarded.')
    return Response(render_docx(open_snapshot(row['content'])['text']),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="zoin-tailored-resume.docx"','Cache-Control':'no-store'})


@router.post('/process',status_code=202)
def process_automation(request: Request, tasks: BackgroundTasks, user_id: CurrentUser):
    enabled()
    from services.automation import remember_session,process_accounts
    remember_session(user_id,request.state.access_token)
    tasks.add_task(process_accounts)
    return {'status':'queued'}


@router.get('/analytics')
def application_analytics(request: Request, user_id: CurrentUser, days: str='30'):
    if days not in ('7','30','90','all'):raise HTTPException(422,'Choose 7, 30, 90 days or all.')
    from datetime import datetime,timezone,timedelta
    client=user_client(request.state.access_token)
    def read_all(table,columns,order):
        result=[]
        for offset in range(0,100000,1000):
            batch=client.table(table).select(columns).eq('user_id',user_id).order(order).range(offset,offset+999).execute().data
            result.extend(batch)
            if len(batch)<1000:return result
        raise HTTPException(422,'Analytics exceeds the local reporting limit.')
    rows=read_all('applications','id,status,submitted_at,created_at,receipt_confirmed_at','id')
    events=read_all('application_events','application_id,to_status,event_type,happened_at','happened_at')
    history={}
    for e in events:history.setdefault(e['application_id'],[]).append(e)
    cutoff=datetime.now(timezone.utc)-timedelta(days=int(days)) if days!='all' else None
    cohort=[]
    weekly=[0,0,0,0]
    for row in rows:
        timeline=history.get(row['id'],[])
        confirmed=bool(row.get('receipt_confirmed_at')) or any(e.get('to_status')=='applied' and e.get('event_type')!='receipt_corrected' for e in timeline)
        if row['status'] in ('found','draft') or not confirmed:continue
        stamp=row.get('submitted_at') or min((e['happened_at'] for e in timeline if e.get('to_status')=='applied'),default=row['created_at'])
        if cutoff and datetime.fromisoformat(stamp.replace('Z','+00:00'))<cutoff:continue
        cohort.append(row)
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()/86400
        if 0<=age<28:weekly[3-int(age//7)]+=1
    ids={r['id'] for r in cohort}
    milestones=lambda status:sum(row['status']==status or any(e.get('to_status')==status for e in history.get(row['id'],[])) for row in cohort)
    from services.agent_applications import attempts
    from core.config import get_settings
    agent_ids={a['application_id'] for a in attempts(user_id) if a['state']=='submitted'} if get_settings().discovery_enabled else set()
    return {'weekly_submissions':weekly,'applied':len(cohort),'interview':milestones('interview'),'offer':milestones('offer'),'rejected':milestones('rejected'),'agent':len(ids&agent_ids),'stages':{s:sum(r['status']==s for r in cohort) for s in ('applied','assessment','interview','offer','rejected','withdrawn','failed')}}


@router.get('/applications/{identifier}/questions')
def get_job_questions(identifier: UUID, request: Request, user_id: CurrentUser):
    enabled()
    from services.application_questions import read
    return read(user_client(request.state.access_token),user_id,identifier)


from services.application_questions import AnswerSubmission

@router.put('/applications/{identifier}/questions')
def save_job_questions(identifier: UUID, payload: AnswerSubmission, request: Request, tasks: BackgroundTasks, user_id: CurrentUser):
    enabled()
    from services.application_questions import save
    from services.automation import remember_session,process_accounts
    result=save(user_client(request.state.access_token),user_id,identifier,payload)
    remember_session(user_id,request.state.access_token)
    tasks.add_task(process_accounts)
    return result


@router.post('/applications/{identifier}/inspect')
def inspect_job_form(identifier: UUID,request: Request,user_id: CurrentUser):
    enabled()
    from services.form_inspection import inspect
    person=getattr(request.state,'verified_user',None)
    identity={'email':person.email,'name':(person.user_metadata or {}).get('name')} if person else None
    return inspect(user_client(request.state.access_token),user_id,identifier,identity=identity)


@router.post('/applications/{identifier}/retry',status_code=202)
def retry_job_form(identifier: UUID,request: Request,tasks: BackgroundTasks,user_id: CurrentUser):
    enabled()
    from services.application_questions import application
    from services.agent_applications import retry_blocked
    from services.automation import remember_session,process_accounts
    job=application(user_client(request.state.access_token),user_id,identifier)
    if job['status']!='found' or job.get('submitted_at') or job.get('receipt_confirmed_at'):raise HTTPException(409,'This job is already submitted or no longer Found.')
    result=retry_blocked(user_id,identifier)
    remember_session(user_id,request.state.access_token);tasks.add_task(process_accounts)
    return result
