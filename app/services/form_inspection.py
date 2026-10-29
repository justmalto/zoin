"""Owned, read-only site inspection and persistent actionable reasons."""
from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from services import discovery_store as store
from services.application_questions import application,register,source_revision
from services.browser_applications import application_browser,FormBlocked


def setup(db):
    db.execute('''create table if not exists agent_form_inspections(owner text not null,application_id text not null,
     source_revision text not null,state text not null,code text,note text not null,updated_at text not null,
     primary key(owner,application_id))''')


def record(owner,job,state,code,note):
    with store.database() as db:
        setup(db)
        db.execute('insert or replace into agent_form_inspections values(?,?,?,?,?,?,?)',(owner,str(job['id']),source_revision(job),state,code,note,store.now().isoformat()))
        db.execute('delete from agent_form_inspections where updated_at<?',((store.now()-timedelta(days=7)).isoformat(),))


def latest(owner,job):
    with store.database() as db:
        setup(db);row=db.execute('select state,code,note,updated_at,source_revision from agent_form_inspections where owner=? and application_id=?',(owner,str(job['id']))).fetchone()
    return {k:row[k] for k in ('state','code','note','updated_at')} if row and row['source_revision']==source_revision(job) else None


def inspect(client,owner,identifier,identity=None):
    job=application(client,owner,identifier)
    if job['status']!='found' or job.get('submitted_at') or job.get('receipt_confirmed_at'):raise HTTPException(409,'This job is already submitted or no longer Found. Inspection will not reapply.')
    from services.agent_applications import attempts
    attempt=next((a for a in attempts(owner) if a.get('application_id')==str(identifier)),None)
    if attempt and attempt['state'] in ('recording','prepared','submitting','submitted','outcome_unknown','needs_reconciliation'):raise HTTPException(409,'An application may already have been submitted. Reconcile its outcome before another attempt.')
    target={'url':job.get('application_url') or '','company':job['company'],'title':job['job_title'],'location':job.get('location')}
    adapter=application_browser()
    try:
        adapter.preflight()
        questions=adapter.questions(target)
        # The browser may take time; re-read ownership/source/submission before storing its result.
        fresh=application(client,owner,identifier)
        if source_revision(fresh)!=source_revision(job) or fresh.get('submitted_at') or fresh.get('receipt_confirmed_at') or fresh['status']!='found':raise HTTPException(409,'The job changed during inspection. Reload it before continuing.')
        result=register(client,owner,fresh,questions,identity=identity)
        record(owner,fresh,'inspected',None,'The actual application form was inspected. No application was submitted.')
        return result|{'inspection':latest(owner,fresh)}
    except FormBlocked as failure:
        record(owner,job,'blocked',failure.code,failure.message)
    except HTTPException as failure:
        if failure.status_code!=503:raise
        record(owner,job,'blocked','BROWSER_UNAVAILABLE',str(failure.detail))
    except Exception:
        record(owner,job,'blocked','SITE_COULD_NOT_BE_READ','The browser could not finish reading this application form. The site may be unavailable; retry inspection or open the posting manually.')
    from services.application_questions import read
    return read(client,owner,identifier)|{'inspection':latest(owner,job)}
