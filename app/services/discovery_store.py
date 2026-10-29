"""Private local SQLite queue. No Supabase mutations or stored access tokens."""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import BoundedSemaphore
from uuid import uuid4
from zoneinfo import ZoneInfo
from core.config import get_settings
from services.job_discovery import discover
from services.groq_client import AIError

SLOT = BoundedSemaphore(1)


def now(): return datetime.now(timezone.utc)
def day(): return now().astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()


@contextmanager
def database():
    path = Path(get_settings().discovery_db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
    os.chmod(path, 0o600)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''
        create table if not exists discovery_accounts(owner text primary key, snapshot text not null, updated_at text not null);
        create table if not exists discovery_runs(id text primary key, owner text not null, day text not null, fingerprint text not null, status text not null, created_at text not null, finished_at text, result text, error text, unique(owner,day,fingerprint));
        create table if not exists discovery_ai_cache(owner text not null, cache_key text not null, value text not null, created_at text not null, primary key(owner,cache_key));
        create index if not exists discovery_owner_date on discovery_runs(owner,created_at);
        ''')
        columns={r[1] for r in db.execute('pragma table_info(discovery_runs)')}
        if 'retry_at' not in columns:db.execute('alter table discovery_runs add column retry_at text')
        if 'snapshot' not in columns:db.execute('alter table discovery_runs add column snapshot text')
        yield db
        db.commit()
    finally: db.close()


def public_run(row):
    if row is None: return None
    value = dict(row)
    value['search_preferences']=json.loads(value['snapshot']).get('preferences') if value.get('snapshot') else None
    value.pop('snapshot',None); value.pop('owner', None); value.pop('fingerprint', None)
    value['result'] = json.loads(value['result']) if value['result'] else None
    return value


def cleanup(db):
    cutoff = (now() - timedelta(days=7)).isoformat()
    retained=[]
    if db.execute("select 1 from sqlite_master where type='table' and name='agent_attempts'").fetchone() and 'run_id' in {r[1] for r in db.execute('pragma table_info(agent_attempts)')}:
        retained=[r[0] for r in db.execute("select run_id from agent_attempts where state in ('needs_answers','retry_ready') and run_id is not null")]
    keep=','.join('?' for _ in retained) or "''"
    if db.execute("select 1 from sqlite_master where type='table' and name='agent_drafts'").fetchone():
        db.execute(f"delete from discovery_runs where created_at < ? and id not in (select run_id from agent_drafts where state!='discarded') and id not in ({keep})",(cutoff,*retained))
    else:db.execute(f'delete from discovery_runs where created_at < ? and id not in ({keep})', (cutoff,*retained))
    # Saved preferences survive transient run retention.
    db.execute('delete from discovery_ai_cache where created_at < ?', (cutoff,))
    db.execute("update discovery_runs set status='queued' where status='cooldown' and retry_at<=?",(now().isoformat(),))
    expired = (now() - timedelta(minutes=10)).isoformat()
    db.execute("update discovery_runs set status='failed', error='Local search was interrupted. Run discovery again.', finished_at=? where status='running' and created_at < ?", (now().isoformat(), expired))


def configure(owner, profiles, preferences, excluded, *, resumes=None):
    snapshot = {'profiles': profiles, 'preferences': preferences, 'excluded': excluded}
    if resumes is not None:
        from services.discovery_vault import seal
        snapshot['_sealed_resumes']=seal(resumes)
        snapshot['_resume_hashes']=[{'id':str(r['id']),'hash':hashlib.sha256((r.get('resume_text') or '')[:18000].encode()).hexdigest()} for r in resumes]
    with database() as db:
        cleanup(db)
        previous=db.execute('select snapshot from discovery_accounts where owner=?',(owner,)).fetchone()
        if previous:snapshot['schedule_saved_at']=json.loads(previous['snapshot']).get('schedule_saved_at',now().isoformat())
        db.execute('insert into discovery_accounts values(?,?,?) on conflict(owner) do update set snapshot=excluded.snapshot,updated_at=excluded.updated_at', (owner, json.dumps(snapshot), now().isoformat()))
    return snapshot


def enqueue(owner, snapshot):
    inputs={'resumes':snapshot.get('_resume_hashes') or [{'id':p['id'],'hash':p['hash']} for p in snapshot['profiles']],
            'preferences':snapshot['preferences'],'excluded':snapshot['excluded']}
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    with database() as db:
        cleanup(db)
        old = db.execute('select * from discovery_runs where owner=? and day=? and fingerprint=? order by created_at desc limit 1', (owner, day(),fingerprint)).fetchone()
        if old and old['status'] != 'failed': return public_run(old)
        if old:
            db.execute('delete from discovery_runs where id=?', (old['id'],))
        active = db.execute("select * from discovery_runs where owner=? and status in ('queued','running') order by created_at desc limit 1", (owner,)).fetchone()
        if active: return public_run(active)
        identifier = str(uuid4())
        db.execute('insert into discovery_runs(id,owner,day,fingerprint,status,created_at) values(?,?,?,?,?,?)', (identifier, owner, day(), fingerprint, 'queued', now().isoformat()))
        db.execute('update discovery_runs set snapshot=? where id=?',(json.dumps(snapshot),identifier))
        return public_run(db.execute('select * from discovery_runs where id=?', (identifier,)).fetchone())


def latest(owner):
    with database() as db:
        cleanup(db)
        account = db.execute('select * from discovery_accounts where owner=?', (owner,)).fetchone()
        row = db.execute("select * from discovery_runs where owner=? and coalesce(json_extract(result,'$.origin'),'')!='found_followup' order by created_at desc limit 1", (owner,)).fetchone()
        snapshot = json.loads(account['snapshot']) if account else None
    return {'run': public_run(row), 'preferences': snapshot['preferences'] if snapshot else None,
            'profiles': snapshot['profiles'] if snapshot else [], 'today': day(),
            'local_only': True, 'capabilities': {'submission_adapters': ['public_browser','greenhouse','local_mock'], 'real_employer_submission': True}, 'snapshot_updated_at': account['updated_at'] if account else None}


def erase(owner):
    with database() as db:
        db.execute('delete from discovery_accounts where owner=?', (owner,))
        db.execute('delete from discovery_runs where owner=?', (owner,))
        db.execute('delete from discovery_ai_cache where owner=?', (owner,))


def process_one(runner=None):
    if not SLOT.acquire(blocking=False): return False
    try:
        with database() as db:
            cleanup(db)
            db.commit()
            db.execute('begin immediate')
            row = db.execute("select * from discovery_runs where status='queued' order by created_at limit 1").fetchone()
            if not row: return False
            account = db.execute('select snapshot from discovery_accounts where owner=?', (row['owner'],)).fetchone()
            if not account:
                db.execute('delete from discovery_runs where id=?', (row['id'],)); return False
            snapshot = json.loads(row['snapshot'] or account['snapshot'])
            db.execute("update discovery_runs set status='running' where id=? and status='queued'", (row['id'],))
        try:
            # Exclude earlier recommendations to avoid offering the same four every day.
            excluded = list(snapshot['excluded'])
            with database() as db:
                previous = db.execute("select result from discovery_runs where owner=? and status='complete' and day<>?", (row['owner'], row['day'])).fetchall()
                for prior in previous:
                    excluded.extend(j['url'] for j in json.loads(prior['result']).get('jobs', []))
            if runner:
                result=runner(snapshot['profiles'],snapshot['preferences'],excluded)
            else:
                from services.discovery_vault import open_snapshot
                from services.job_discovery import profile_resumes
                resumes=open_snapshot(snapshot['_sealed_resumes']) if snapshot.get('_sealed_resumes') else []
                cache=lambda action,key,value:ai_cache(row['owner'],action,key,value)
                profiles=profile_resumes(resumes,cache=cache)
                with database() as db:
                    current=db.execute('select snapshot from discovery_accounts where owner=?',(row['owner'],)).fetchone()
                    if current:
                        fresh=json.loads(current['snapshot']);fresh['profiles']=profiles
                        db.execute('update discovery_accounts set snapshot=? where owner=?',(json.dumps(fresh),row['owner']))
                result=discover(profiles,snapshot['preferences'],excluded,resumes=resumes,cache=cache)
            with database() as db:
                db.execute("update discovery_runs set status='complete',result=?,finished_at=? where id=?", (json.dumps(result), now().isoformat(), row['id']))
        except AIError as error:
            with database() as db:
                if error.status_code==429:
                    retry=(now()+timedelta(seconds=max(30,error.retry_after or 60))).isoformat()
                    db.execute("update discovery_runs set status='cooldown',error=?,retry_at=? where id=?",('Groq quota cooldown. Cached completed stages are retained; the local worker will retry.',retry,row['id']))
                else:
                    db.execute("update discovery_runs set status='failed',error=?,finished_at=? where id=?",('AI profiling or assessment could not complete. No applications were submitted.',now().isoformat(),row['id']))
        except Exception:
            # Provider/input content and credentials never become error text.
            with database() as db:
                db.execute("update discovery_runs set status='failed',error=?,finished_at=? where id=?", ('Discovery could not finish. Retry; your tracked applications were not changed.', now().isoformat(), row['id']))
        return True
    finally: SLOT.release()


def scheduler_loop(stop):
    while not stop.is_set():
        try:
            with database() as db:
                cleanup(db)
                accounts = db.execute('select owner,snapshot from discovery_accounts').fetchall()
            for account in accounts:
                snapshot = json.loads(account['snapshot'])
                if schedule_due(account['owner'], snapshot):
                    enqueue(account['owner'], snapshot)
            process_one()
            from services.automation import process_accounts
            process_accounts()
        except Exception:
            pass  # API still exposes persisted run status; never log profile contents.
        stop.wait(30)


def ai_cache(owner, action, key, value):
    with database() as db:
        if action=='get':
            row=db.execute('select value from discovery_ai_cache where owner=? and cache_key=?',(owner,key)).fetchone()
            return json.loads(row['value']) if row else None
        db.execute('insert or replace into discovery_ai_cache values(?,?,?,?)',(owner,key,json.dumps(value),now().isoformat()))


def schedule_due(owner, snapshot):
    prefs=snapshot['preferences']
    if not prefs.get('daily_enabled') or prefs.get('paused') or prefs.get('frequency_days',1) is None:return False
    with database() as db:
        row=db.execute("select created_at from discovery_runs where owner=? and coalesce(json_extract(result,'$.origin'),'')!='found_followup' order by created_at desc limit 1",(owner,)).fetchone()
    base=max(filter(None,[snapshot.get('schedule_saved_at'),row['created_at'] if row else None]),default=None)
    return base is None or now()>=datetime.fromisoformat(base)+timedelta(days=prefs.get('frequency_days',1))


def save_preferences(owner, preferences):
    with database() as db:
        account=db.execute('select snapshot from discovery_accounts where owner=?',(owner,)).fetchone()
        snapshot=json.loads(account['snapshot']) if account else {'profiles':[],'excluded':[]}
        previous=snapshot.get('preferences',{})
        if previous.get('frequency_days')!=preferences.get('frequency_days') or previous.get('daily_enabled')!=preferences.get('daily_enabled'):
            snapshot['schedule_saved_at']=now().isoformat()
        snapshot['preferences']=preferences
        db.execute('insert into discovery_accounts values(?,?,?) on conflict(owner) do update set snapshot=excluded.snapshot,updated_at=excluded.updated_at',(owner,json.dumps(snapshot),now().isoformat()))
    return latest(owner)
