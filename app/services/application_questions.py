"""Per-application questions and conservative, account-private answer reuse."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from pydantic import Field
from schemas.ai import StrictModel

class Answer(StrictModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=4000)
    remember: bool = False

class AnswerSubmission(StrictModel):
    revision: str = Field(min_length=1, max_length=64)
    answers: list[Answer] = Field(max_length=50)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def application(client, owner, identifier):
    rows=client.table('applications').select('*').eq('id',str(identifier)).eq('user_id',owner).execute().data
    if not rows:raise HTTPException(404,'Application not found.')
    return rows[0]


def source_revision(job):
    return fingerprint([job.get(k) for k in ('application_url','company','job_description')])


def memory_key(question):
    # Exact wording + options + explicit context, never broad fuzzy matching.
    # Employer-bound questions must always carry an employer in context.
    return fingerprint({k:question.get(k) for k in ('label','options','type')}|{'context':{k:v for k,v in question.get('context',{}).items() if k!='form_url'}})


def validate_questions(questions):
    if not isinstance(questions,list) or len(questions)>50:raise ValueError('Invalid question count')
    seen=set();result=[]
    for q in questions:
        key=q.get('key','');label=q.get('label','');kind=q.get('type','text');options=q.get('options',[])
        if not isinstance(key,str) or not key or len(key)>200 or key in seen:raise ValueError('Invalid field key')
        if not isinstance(label,str) or not label or len(label)>2000 or kind not in ('text','select'):raise ValueError('Invalid field')
        if re.search(r'password|passcode|verification code|one.time|social security|bank account|credit card',label,re.I):raise ValueError('Sensitive credential question needs manual handling')
        if not isinstance(options,list) or len(options)>400 or any(not isinstance(x,str) or not x or len(x)>500 for x in options) or (kind=='select' and not options):raise ValueError('Invalid options')
        context=q.get('context',{})
        if not isinstance(context,dict) or any(k not in ('country','employer','form_url') or not isinstance(v,str) or len(v)>2000 for k,v in context.items()):raise ValueError('Invalid context')
        seen.add(key)
        result.append({'key':key,'label':label,'type':kind,'options':options,'required':bool(q.get('required',True)),'context':context})
    return result


def register(client,owner,job,questions,identity=None):
    questions=validate_questions(questions)
    revision=fingerprint([source_revision(job),questions])
    rows=client.table('application_agent_questions').select('*').eq('user_id',owner).eq('application_id',job['id']).execute().data
    if rows and rows[0]['revision']==revision:return rows[0]
    memories=client.table('application_answer_memory').select('question_key,answer').eq('user_id',owner).execute().data
    by_key={r['question_key']:r['answer'] for r in memories}
    for q in questions:
        q['memory_key']=memory_key(q)
        value=by_key.get(q['memory_key'])
        if value is None and identity:
            from services.browser_applications import base_answer
            value=base_answer({'key':q['key']},identity) if q['key'] in ('email','full_name') else None
        if value is not None and (q['type']!='select' or value in q['options']):q.update(answer=value,remember=q['memory_key'] in by_key,source='saved_answer' if q['memory_key'] in by_key else 'account_profile')
    row={'application_id':str(job['id']),'user_id':owner,'revision':revision,'source_revision':source_revision(job),'questions':questions,'state':'ready' if all(not q['required'] or q.get('answer') for q in questions) else 'needs_answers','updated_at':datetime.now(timezone.utc).isoformat()}
    # Reused answers are shown for confirmation before the agent continues.
    return client.table('application_agent_questions').upsert(row,on_conflict='application_id').execute().data[0]


def read(client,owner,identifier):
    job=application(client,owner,identifier)
    rows=client.table('application_agent_questions').select('*').eq('user_id',owner).eq('application_id',str(identifier)).execute().data
    from services.form_inspection import latest as latest_inspection
    inspection=latest_inspection(owner,job)
    from services.agent_applications import attempts
    attempt=next((a for a in attempts(owner) if a.get('application_id')==str(identifier)),None)
    if not rows:
        from services.browser_applications import application_browser
        supported=application_browser().supports({'url':job.get('application_url') or ''})
        return {'state':'not_inspected' if supported else 'unsupported','questions':[],'revision':None,'inspection':inspection,'attempt':attempt}
    row=rows[0]
    if row['source_revision']!=source_revision(job):return {'state':'stale','questions':[],'revision':None,'inspection':inspection,'attempt':attempt}
    return row|{'inspection':inspection,'attempt':attempt}


def save(client,owner,identifier,payload):
    job=application(client,owner,identifier)
    if job.get('submitted_at') or job.get('receipt_confirmed_at') or job['status']!='found':raise HTTPException(409,'This application is already submitted or no longer Found.')
    current=read(client,owner,identifier)
    if not current.get('revision') or current['revision']!=payload.revision:raise HTTPException(409,'The form changed. Reload the questions before saving.')
    supplied={a.key:a for a in payload.answers}
    if len(supplied)!=len(payload.answers) or set(supplied)!={q['key'] for q in current['questions']}:raise HTTPException(422,'Answer the current form questions only.')
    for q in current['questions']:
        answer=supplied[q['key']].value.strip()
        if q['required'] and not answer:raise HTTPException(422,'Complete all required questions.')
        if answer and q['type']=='select' and answer not in q['options']:raise HTTPException(422,'Choose one of the employer’s options.')
    try:
        response=client.rpc('save_application_agent_answers',{'p_application_id':str(identifier),'p_revision':payload.revision,'p_expected_updated_at':job['updated_at'],'p_answers':[a.model_dump() for a in payload.answers]}).execute().data
    except Exception as exc:
        # Never turn a database error into an apparent successful resume.
        raise HTTPException(409,'Answers were not saved. Reload the form and try again.') from exc
    return response


def followup_due(job,prefs,known_urls=(),now=None):
    if not prefs.get('found_followup_enabled') or prefs.get('paused') or prefs.get('automation_level','recommend')=='recommend':return False
    from services.job_discovery import canonical
    url=job.get('application_url')
    if (canonical(url) or url) in {canonical(u) or u for u in known_urls}:return False
    if job.get('status')!='found' or job.get('submitted_at') or job.get('receipt_confirmed_at') or not job.get('application_url'):return False
    hours=prefs.get('found_followup_hours',24)
    if hours not in (12,24,50,72):return False
    try:return (now or datetime.now(timezone.utc))>=datetime.fromisoformat(job['created_at'].replace('Z','+00:00'))+timedelta(hours=hours)
    except (ValueError,KeyError,TypeError):return False


def followup_label(job,prefs,attempt=None):
    if not prefs.get('found_followup_enabled') or job.get('status')!='found':return None
    if job.get('submitted_at') or job.get('receipt_confirmed_at') or (attempt and attempt.get('state') in ('submitted','submitting','recording','prepared','outcome_unknown','needs_reconciliation')):
        return {'state':'excluded','eligible_at':None}
    if attempt and attempt.get('state')=='needs_answers':return {'state':'needs_answers','eligible_at':None}
    from services.browser_applications import application_browser
    if not application_browser().supports({'url':job.get('application_url') or ''}):return {'state':'unsupported','eligible_at':None}
    try:stamp=datetime.fromisoformat(job['created_at'].replace('Z','+00:00'))+timedelta(hours=prefs.get('found_followup_hours',24))
    except (ValueError,KeyError,TypeError):return None
    return {'state':'paused' if prefs.get('paused') or prefs.get('automation_level','recommend')=='recommend' else 'scheduled','eligible_at':stamp.isoformat()}
