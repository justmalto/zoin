"""Owner-scoped local automation. Short-lived session leases; no refresh tokens."""
import hashlib
import json
from datetime import timedelta
from threading import BoundedSemaphore, Lock
from uuid import uuid4
from fastapi import HTTPException
from services import discovery_store as store
from services.discovery_vault import seal, open_snapshot

LEASES={}
LEASE_LOCK=Lock()
WORKER=BoundedSemaphore(1)
DRAFT_SLOT=BoundedSemaphore(1)


def setup(db):
    db.execute('''create table if not exists agent_drafts(id text primary key, owner text not null,
      run_id text not null, job_url text not null, application_id text, state text not null,
      content text not null, created_at text not null, updated_at text not null, unique(owner,run_id,job_url))''')
    db.execute('''create table if not exists automation_events(id text primary key, owner text not null,
      job_url text, stage text not null, note text not null, created_at text not null)''')


def remember_session(owner, token):
    # Only memory, bounded to one hour; provider still validates the JWT before any worker action.
    with LEASE_LOCK:
        expired=[key for key,value in LEASES.items() if value[1]<=store.now()]
        for key in expired:LEASES.pop(key,None)
        LEASES[owner]=(token,store.now()+timedelta(hours=1))


def event(owner,url,stage,note):
    with store.database() as db:
        setup(db)
        last=db.execute('select stage,note from automation_events where owner=? and job_url=? order by created_at desc limit 1',(owner,url)).fetchone()
        if last and last['stage']==stage and last['note']==note:return
        db.execute('insert into automation_events values(?,?,?,?,?,?)',(str(uuid4()),owner,url,stage,note,store.now().isoformat()))
        db.execute('delete from automation_events where created_at<?',((store.now()-timedelta(days=7)).isoformat(),))


def public_draft(row, include_text=False):
    result={k:row[k] for k in ['id','run_id','job_url','application_id','state','created_at','updated_at']}
    value=open_snapshot(row['content'])
    result.update({k:value[k] for k in ['file_name','assessment','source_resume_id']})
    if include_text:result.update({k:value[k] for k in ['text','edits','unaddressed_gaps']})
    return result


def draft(owner,identifier):
    with store.database() as db:
        setup(db);row=db.execute('select * from agent_drafts where owner=? and id=?',(owner,identifier)).fetchone()
    if not row:raise HTTPException(404,'Attached draft not found.')
    return row


def usage(owner):
    from services.agent_applications import setup as attempts_setup
    with store.database() as db:
        attempts_setup(db)
        rows=db.execute("select created_at from agent_attempts where owner=? and state not in ('needs_user_action','needs_reconciliation','needs_answers','blocked_before_submission','retry_ready')",(owner,)).fetchall()
    from zoneinfo import ZoneInfo
    from datetime import datetime
    return sum(datetime.fromisoformat(row['created_at']).astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()==store.day() for row in rows)


def activity(owner, client=None):
    from services.agent_applications import attempts
    with store.database() as db:
        setup(db)
        drafts=[public_draft(r) for r in db.execute('select * from agent_drafts where owner=? order by created_at desc',(owner,))]
        events=[dict(r) for r in db.execute('select job_url,stage,note,created_at from automation_events where owner=? order by created_at desc limit 100',(owner,))]
    with LEASE_LOCK:lease=LEASES.get(owner)
    records=attempts(owner)
    if client:
        applications=client.table('applications').select('id,status,receipt_confirmed_at').eq('user_id',owner).execute().data
        by_id={str(a['id']):a for a in applications}
        records=[a|{'tracking_status':by_id.get(a.get('application_id'),{}).get('status'),'email_confirmed':bool(by_id.get(a.get('application_id'),{}).get('receipt_confirmed_at'))} for a in records]
    return {'drafts':drafts,'events':events,'attempts':records,'used_today':usage(owner),
            'day':store.day(),'timezone':'Asia/Kolkata','worker_session_available':bool(lease and lease[1]>store.now()),
            'adapter_scope':'Public HTTPS application links are inspected with Playwright. Standard résumé forms are supported; login, CAPTCHA and custom controls pause with a specific reason.'}


def create_draft(owner,run_id,url,client):
    if not DRAFT_SLOT.acquire(blocking=False):raise HTTPException(409,'Another résumé draft is being prepared. Retry shortly.')
    try:return _create_draft(owner,run_id,url,client)
    finally:DRAFT_SLOT.release()


def _create_draft(owner,run_id,url,client):
    from services.agent_applications import find_recommendation
    from services.resume_tailoring import propose
    from services.career_agent import requirements_for,assess
    job,prefs=find_recommendation(owner,run_id,url)
    with store.database() as db:
        setup(db);existing=db.execute('select * from agent_drafts where owner=? and run_id=? and job_url=?',(owner,run_id,url)).fetchone()
    if existing:return public_draft(existing,True)
    rows=client.table('resumes').select('id,resume_text').eq('id',job['resume_id']).eq('user_id',owner).execute().data
    if not rows:raise HTTPException(409,'Source résumé is unavailable. Search again.')
    source=rows[0]['resume_text'][:18000]
    if hashlib.sha256(source.encode()).hexdigest()!=job['resume_hash']:raise HTTPException(409,'Source résumé changed. Search again.')
    cache=lambda action,key,value:store.ai_cache(owner,action,key,value)
    event(owner,url,'tailoring','Creating a temporary tailored résumé from source evidence.')
    proposal=propose(source,job['description'],job.get('assessment',{}).get('suggestions',[]),cache=cache)
    # A second evidence-bound assessment gates the exact document, never the original score.
    assessment=assess(job['description'],proposal['text'],requirements_for(job['description'],cache=cache),threshold=prefs.get('matching_threshold',80),cache=cache)
    content=proposal|{'assessment':assessment,'file_name':'zoin-tailored-resume.docx','source_resume_id':job['resume_id'],'source_hash':job['resume_hash']}
    existing_app=client.table('applications').select('id').eq('user_id',owner).eq('application_url',url).execute().data
    if existing_app:application_id=str(existing_app[0]['id'])
    else:
        from schemas.application import ApplicationCreate
        payload=ApplicationCreate(company=job['company'],job_title=job['title'],job_description=job['description'],application_url=url,selected_resume_id=job['resume_id'],location=job.get('location'))
        created=client.rpc('create_application',{'p_payload':payload.model_dump(mode='json')}).execute().data
        application_id=str((created[0] if isinstance(created,list) else created)['id'])
    identifier=str(uuid4());stamp=store.now().isoformat()
    with store.database() as db:
        setup(db)
        db.execute('insert or ignore into agent_drafts values(?,?,?,?,?,?,?,?,?)',(identifier,owner,run_id,url,application_id,'awaiting_approval' if prefs.get('automation_level')=='review_resume' else 'ready',seal(content),stamp,stamp))
        row=db.execute('select * from agent_drafts where owner=? and run_id=? and job_url=?',(owner,run_id,url)).fetchone()
    event(owner,url,'awaiting_approval' if prefs.get('automation_level')=='review_resume' else 'reassessed','Tailored résumé saved privately and reassessed. Mandatory gaps still block submission.')
    return public_draft(row,True)


def discard(owner,identifier):
    row=draft(owner,identifier)
    from services.agent_applications import attempts
    if row['state']=='submitted' or any(a['job_url']==row['job_url'] and a['state'] in ('recording','prepared','submitting','submitted','outcome_unknown') for a in attempts(owner)):
        raise HTTPException(409,'A résumé attached to a submission cannot be discarded.')
    with store.database() as db:
        # Keep a tombstone to avoid automatic re-creation after the user discards it.
        db.execute("update agent_drafts set state='discarded',content=?,updated_at=? where id=? and owner=?",(seal({'text':'','edits':[],'unaddressed_gaps':[],'file_name':'discarded','assessment':{},'source_resume_id':''}),store.now().isoformat(),identifier,owner))
    event(owner,row['job_url'],'discarded','Temporary draft discarded. No submission requested.')


def submit_draft(owner,identifier,client,applicant,adapter,*,approve=False):
    from services.agent_applications import submit,find_recommendation
    row=draft(owner,identifier);content=open_snapshot(row['content'])
    job,prefs=find_recommendation(owner,row['run_id'],row['job_url'])
    if row['state']=='discarded':raise HTTPException(409,'Draft was discarded.')
    if prefs.get('paused'):raise HTTPException(409,'Automation is paused.')
    if prefs.get('automation_level')=='review_resume' and not approve and row['state']!='approved_needs_answers':raise HTTPException(409,'This draft needs your approval.')
    if prefs.get('automation_level') not in ('review_resume','tailored_resume'):raise HTTPException(409,'Select tailored résumé automation first.')
    result=submit(owner,row['run_id'],row['job_url'],client,applicant,adapter=adapter,tailored=content)
    with store.database() as db:
        db.execute('update agent_drafts set state=?,application_id=?,updated_at=? where id=? and owner=?',('approved_needs_answers' if result['state'] in ('needs_answers','blocked_before_submission') and prefs.get('automation_level')=='review_resume' else result['state'],result.get('application_id'),store.now().isoformat(),identifier,owner))
    return result


def process_owner(owner,client,applicant,adapter):
    current=store.latest(owner);prefs=current['preferences'] or {};run=current['run']
    if prefs.get('paused') or prefs.get('automation_level','recommend')=='recommend':return
    from services.agent_applications import submit,attempts
    known={a['job_url'] for a in attempts(owner) if a['state'] not in ('needs_answers','retry_ready')}
    from services.agent_applications import setup as setup_attempts
    with store.database() as db:
        setup_attempts(db)
        pending=[store.public_run(r) for r in db.execute("select * from discovery_runs where owner=? and status='complete' and id in (select run_id from agent_attempts where owner=? and state in ('needs_answers','retry_ready'))",(owner,owner))]
    try:followups=found_followups(owner,client,prefs,adapter)
    except Exception:
        event(owner,'account','followup_waiting','Found follow-up could not finish. Existing recommendation processing can continue; retry after checking provider availability.')
        followups=[]
    runs=([run] if run and run['status']=='complete' else [])+pending+followups
    runs=list({r['id']:r for r in runs}.values())
    for run in runs:
        for job in run['result'].get('jobs',[]):
            if job['url'] in known:continue
            try:
                latest=store.latest(owner)['preferences'] or {}
                if latest.get('paused'):break
                if latest.get('automation_level')!='review_resume' and (not adapter or not adapter.supports(job)):
                    event(owner,job['url'],'unsupported','This employer has no enabled submission adapter. Apply manually.');continue
                if not job.get('assessment',{}).get('eligible_for_automation'):
                    event(owner,job['url'],'blocked','Mandatory requirement checks did not pass.');continue
                level=latest.get('automation_level')
                if level=='existing_resume':result=submit(owner,run['id'],job['url'],client,applicant,adapter=adapter)
                elif level in ('review_resume','tailored_resume'):
                    value=create_draft(owner,run['id'],job['url'],client)
                    if value['state']=='discarded' or (level=='review_resume' and value['state']!='approved_needs_answers'):continue
                    result=submit_draft(owner,value['id'],client,applicant,adapter)
                else:break
                event(owner,job['url'],result['state'],result['note'])
                if result['state'] in ('submitted','outcome_unknown') and result.get('application_id'):
                    from services.mail_sync import configured,sync_account
                    if configured():sync_account(owner,'job_saved',result['application_id'])
            except HTTPException as failure:event(owner,job['url'],'blocked',str(failure.detail))
            except Exception as failure:
                from services.groq_client import AIError
                if isinstance(failure,AIError):
                    event(owner,job['url'],'cooldown' if failure.status_code==429 else 'generation_blocked',str(failure))
                else:event(owner,job['url'],'waiting','Automation could not complete this stage. Retry after checking provider availability.')

def process_accounts():
    if not WORKER.acquire(blocking=False):return
    try:
        from core.supabase import user_client,auth_client
        from core.config import get_settings
        from services.browser_applications import application_browser
        with LEASE_LOCK:leases=list(LEASES.items())
        for owner,(token,expires) in leases:
            if expires<=store.now():continue
            try:
                user=auth_client().auth.get_user(token).user
                if not user or str(user.id)!=owner:continue
                settings=get_settings();adapter=application_browser()
                client=user_client(token)
                name=user.user_metadata.get('name') or user.user_metadata.get('display_name')
                if not name:
                    rows=client.table('profiles').select('display_name').eq('user_id',owner).execute().data
                    name=rows[0].get('display_name') if rows else None
                if not name:
                    event(owner,'account','needs_user_action','Set your applicant name before automated submission.');continue
                process_owner(owner,client,{'name':name,'email':user.email},adapter)
            except Exception:continue
    finally:WORKER.release()


def found_followups(owner,client,prefs,adapter):
    """Assess saved Found jobs against all owned résumés, without re-submission."""
    from services.application_questions import followup_due,fingerprint
    from services.agent_applications import attempts
    if not prefs.get('found_followup_enabled'):return []
    known={a['job_url'] for a in attempts(owner) if a['state'] not in ('needs_answers','retry_ready')}
    rows=client.table('applications').select('*').eq('user_id',owner).eq('status','found').limit(1000).execute().data
    contexts=[];resumes=None;inspected=0
    for row in rows:
        if not followup_due(row,prefs,known,store.now()):continue
        job={'url':row['application_url'],'company':row['company'],'title':row['job_title'],'description':row['job_description'],'location':row.get('location'),'source':'local_mock' if row['application_url'].startswith('http://127.0.0.1:') else 'saved_link'}
        if not adapter or not adapter.supports(job):
            event(owner,job['url'],'unsupported','Found follow-up: no enabled adapter for this employer. Apply manually.');continue
        if resumes is None:resumes=client.table('resumes').select('id,file_name,resume_text').eq('user_id',owner).limit(10).execute().data
        key=fingerprint([row['id'],job,resumes,prefs])
        with store.database() as db:
            existing=db.execute("select * from discovery_runs where owner=? and fingerprint=? and status='complete'",(owner,key)).fetchone()
        if existing:
            contexts.append(store.public_run(existing));continue
        if store.ai_cache(owner,'get','found-blocked:'+key,None):continue
        if inspected>=3:break
        inspected+=1
        from services.career_agent import requirements_for,assess
        cache=lambda action,key,value:store.ai_cache(owner,action,key,value)
        requirements=requirements_for(job['description'],cache=cache)
        ranked=[]
        for resume in resumes:
            source=(resume.get('resume_text') or '')[:18000]
            if not source:continue
            assessment=assess(job['description'],source,requirements,threshold=prefs.get('matching_threshold',80),cache=cache)
            if assessment['eligible_for_automation']:ranked.append((assessment['score'],str(resume['id']),resume,assessment,source))
        if not ranked:
            store.ai_cache(owner,'put','found-blocked:'+key,{'blocked':True})
            event(owner,job['url'],'blocked','Found follow-up: no saved résumé passes the threshold and mandatory checks.');continue
        _,_,resume,assessment,source=max(ranked,key=lambda x:(x[0],x[1]))
        job.update(resume_id=str(resume['id']),resume_name=resume['file_name'],resume_hash=hashlib.sha256(source.encode()).hexdigest(),assessment=assessment)
        identifier=str(uuid4());stamp=store.now().isoformat()
        with store.database() as db:
            db.execute('insert or ignore into discovery_runs(id,owner,day,fingerprint,status,created_at,finished_at,result,snapshot) values(?,?,?,?,?,?,?,?,?)',(identifier,owner,store.day(),key,'complete',stamp,stamp,json.dumps({'jobs':[job],'origin':'found_followup'}),json.dumps({'preferences':prefs})))
            saved=db.execute('select * from discovery_runs where owner=? and fingerprint=?',(owner,key)).fetchone()
        contexts.append(store.public_run(saved))
        if len(contexts)>=3:break
    return contexts
