"""Single-host durable Groq ledger. Atomic admission; metadata only in usage rows."""
import json
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from core.config import get_settings


class BudgetBlocked(Exception):
    def __init__(self, code, retry_after, message):
        self.code = code; self.retry_after = max(1, math.ceil(retry_after)); self.message = message


def duration(value):
    if not value: return None
    try:
        result=float(value)
        return result if math.isfinite(result) and result>=0 else None
    except (TypeError, ValueError): pass
    pieces = re.findall(r'(\d+(?:\.\d+)?)(ms|d|h|m|s)', str(value))
    if not pieces or ''.join(a+b for a,b in pieces) != value: return None
    return sum(float(a)*{'ms':.001,'s':1,'m':60,'h':3600,'d':86400}[b] for a,b in pieces)


class Budget:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.path = Path(getattr(self.settings,'ai_budget_db_path','') or Path(self.settings.discovery_db_path).with_name('ai-budget.sqlite3'))

    @property
    def background_limit(self):
        return min(self.settings.groq_background_tokens_per_day,int(self.settings.groq_tokens_per_day*.6))

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('begin immediate')
            db.execute('create table if not exists ai_calls(id text primary key,model text,owner text,task text,background integer,created real,tokens integer,estimated integer,prompt_tokens integer,completion_tokens integer,state text)')
            db.execute('create index if not exists ai_calls_model_time on ai_calls(model,created)')
            db.execute('create index if not exists ai_calls_owner_time on ai_calls(owner,created)')
            db.execute('create table if not exists ai_provider(model text,kind text,remaining integer,until real,primary key(model,kind))')
            db.execute('create table if not exists ai_results(owner text,cache_key text,content text,expires real,primary key(owner,cache_key))')
            db.execute('create table if not exists ai_cache_hits(owner text,task text,created real)')
            db.execute('create index if not exists ai_cache_hits_owner_time on ai_cache_hits(owner,created)')
            db.execute('create table if not exists ai_cache_work(owner text,cache_key text,expires real,claim_id text,primary key(owner,cache_key))')
            if 'claim_id' not in {r[1] for r in db.execute('pragma table_info(ai_cache_work)')}:
                db.execute('alter table ai_cache_work add column claim_id text')
            db.execute('create index if not exists ai_results_expiry on ai_results(expires)')
            db.execute('delete from ai_calls where created<?',(time.time()-7*86400,))
            db.execute('delete from ai_cache_hits where created<?',(time.time()-7*86400,))
            db.execute('delete from ai_results where expires<?',(time.time(),))
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
        finally: db.close()

    def reserve(self, model, owner, task, estimate, background=False):
        s = self.settings; stamp = time.time()
        if estimate > s.groq_tokens_per_minute:
            raise BudgetBlocked('request_too_large', 1, 'This AI request exceeds the per-request free-tier token budget. Shorten the input; it was not sent.')
        with self.database() as db:
            db.execute('delete from ai_calls where created<?', (stamp-7*86400,))
            db.execute('delete from ai_results where expires<?', (stamp,))
            for p in db.execute('select * from ai_provider where model=? and until>?', (model,stamp)):
                needed = estimate if p['kind']=='tokens' else 1
                if p['kind']=='cooldown' or p['remaining'] < needed:
                    raise BudgetBlocked('provider_cooldown', p['until']-stamp, 'Groq capacity is cooling down for this model. Retry after the indicated wait.')
            calls = db.execute('select * from ai_calls where model=? and created>?', (model,stamp-86400)).fetchall()
            minute = [r for r in calls if r['created']>stamp-60]
            owned = [r for r in calls if r['owner']==owner]
            background_calls = [r for r in calls if r['background']]
            checks = [
                ('minute_tokens',minute,s.groq_tokens_per_minute,estimate,60,'tokens'),
                ('minute_requests',minute,s.groq_requests_per_minute,1,60,'requests'),
                ('daily_tokens',calls,s.groq_tokens_per_day,estimate,86400,'tokens'),
                ('daily_requests',calls,s.groq_requests_per_day,1,86400,'requests'),
                ('account_daily_tokens',owned,s.groq_owner_tokens_per_day,estimate,86400,'tokens')]
            if background:
                checks.append(('background_daily_tokens',background_calls,self.background_limit,estimate,86400,'tokens'))
            for code,rows,cap,needed,window,unit in checks:
                used = sum(r['tokens'] for r in rows) if unit=='tokens' else len(rows)
                if used + needed > cap:
                    retry = window
                    for row in sorted(rows,key=lambda r:r['created']):
                        used -= row['tokens'] if unit=='tokens' else 1
                        if used + needed <= cap:
                            retry = row['created']+window-stamp; break
                    raise BudgetBlocked(code,retry,'The '+code.replace('_',' ')+' budget is reserved or used. No AI request was sent; retry after the indicated wait.')
            identifier = str(uuid4())
            db.execute('insert into ai_calls values(?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,model,owner,task,int(background),stamp,estimate,1,None,None,'reserved'))
            db.execute("update ai_provider set remaining=max(0,remaining-?) where model=? and kind='tokens' and until>?",(estimate,model,stamp))
            db.execute("update ai_provider set remaining=max(0,remaining-1) where model=? and kind='requests' and until>?",(model,stamp))
            return identifier

    def finish(self, identifier, *, usage=None, status='success', headers=None, retry_after=None):
        stamp=time.time(); headers={k.lower():v for k,v in (headers or {}).items()}
        usage=usage if isinstance(usage,dict) else {}
        def count(name):
            value=usage.get(name)
            return value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=10_000_000 else None
        total=count('total_tokens'); prompt=count('prompt_tokens'); completion=count('completion_tokens')
        if total is None and prompt is not None and completion is not None: total=prompt+completion
        if total is not None and prompt is not None and completion is not None: total=max(total,prompt+completion)
        if status=='rejected': total=0
        with self.database() as db:
            row=db.execute('select * from ai_calls where id=?',(identifier,)).fetchone()
            if not row or row['state']!='reserved': return
            db.execute('update ai_calls set tokens=?,estimated=?,prompt_tokens=?,completion_tokens=?,state=? where id=?',
                (total if total is not None else row['tokens'],int(total is None),prompt,completion,status,identifier))
            for kind in ('tokens','requests'):
                try: remaining=max(0,int(headers['x-ratelimit-remaining-'+kind]))
                except (KeyError,ValueError,TypeError): continue
                reset=duration(headers.get('x-ratelimit-reset-'+kind))
                deadline=stamp+max(1, reset if reset is not None else (60 if kind=='tokens' else 86400))
                old=db.execute('select * from ai_provider where model=? and kind=?',(row['model'],kind)).fetchone()
                if old and old['until']>stamp:
                    refund=max(0,row['tokens']-total) if kind=='tokens' and total is not None else 0
                    remaining=min(remaining,old['remaining']+refund); deadline=max(deadline,old['until'])
                db.execute('insert or replace into ai_provider values(?,?,?,?)',(row['model'],kind,remaining,deadline))
            if retry_after is not None:
                old=db.execute("select until from ai_provider where model=? and kind='cooldown'",(row['model'],)).fetchone()
                deadline=max(stamp+max(1,retry_after),old['until'] if old else 0)
                db.execute("insert or replace into ai_provider values(?,'cooldown',0,?)",(row['model'],deadline))

    def _cipher(self):
        from cryptography.fernet import Fernet
        key=self.path.with_suffix('.key')
        if not key.exists():
            fd=os.open(key,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as stream: stream.write(Fernet.generate_key())
        os.chmod(key,0o600)
        return Fernet(key.read_bytes())

    def cached(self, owner, key, value=None):
        if owner=='_unattributed': return None
        with self.database() as db:
            if value is None:
                row=db.execute('select content from ai_results where owner=? and cache_key=? and expires>?',(owner,key,time.time())).fetchone()
                if not row: return None
                try:
                    envelope=json.loads(self._cipher().decrypt(row['content'].encode()))
                    if envelope['owner']!=owner or envelope['key']!=key:raise ValueError('Cache identity mismatch')
                    return envelope['value']
                except Exception:
                    db.execute('delete from ai_results where owner=? and cache_key=?',(owner,key)); return None
            content=self._cipher().encrypt(json.dumps({'owner':owner,'key':key,'value':value}).encode()).decode()
            db.execute('insert or replace into ai_results values(?,?,?,?)',(owner,key,content,time.time()+86400))

    def claim_cache(self, owner, key):
        with self.database() as db:
            db.execute('delete from ai_cache_work where expires<?',(time.time(),))
            row=db.execute('select expires from ai_cache_work where owner=? and cache_key=?',(owner,key)).fetchone()
            if row:raise BudgetBlocked('identical_request_running',row['expires']-time.time(),'The same analysis is already running. Wait before retrying; another AI call was not sent.')
            lease=str(uuid4())
            db.execute('insert into ai_cache_work values(?,?,?,?)',(owner,key,time.time()+120,lease))
            return lease

    def release_cache(self, owner, key, lease):
        with self.database() as db:db.execute('delete from ai_cache_work where owner=? and cache_key=? and claim_id=?',(owner,key,lease))

    def cache_hit(self, owner, task):
        if owner=='_unattributed':return
        with self.database() as db:db.execute('insert into ai_cache_hits values(?,?,?)',(owner,task,time.time()))

    def forget(self, owner):
        with self.database() as db:
            db.execute('delete from ai_results where owner=?',(owner,))
            db.execute('delete from ai_cache_work where owner=?',(owner,))
            # Clearing discovery cannot reset spent quota; metadata expires in seven days.

    def summary(self, owner):
        stamp=time.time()
        with self.database() as db:
            rows=db.execute('select model,task,count(*) requests,sum(tokens) tokens,sum(estimated) estimated_requests,sum(coalesce(prompt_tokens,0)) prompt_tokens,sum(coalesce(completion_tokens,0)) completion_tokens from ai_calls where owner=? and created>? group by model,task',(owner,stamp-86400)).fetchall()
            provider=db.execute('select model,kind,remaining,until from ai_provider where until>?',(stamp,)).fetchall()
            hits=db.execute('select task,count(*) hits from ai_cache_hits where owner=? and created>? group by task',(owner,stamp-86400)).fetchall()
            shared=db.execute('select model,sum(tokens) tokens,count(*) requests from ai_calls where created>? group by model',(stamp-86400,)).fetchall()
        shared_by_model={r['model']:r['tokens'] for r in shared}
        models=set(shared_by_model)|{self.settings.groq_model,self.settings.groq_utility_model,self.settings.groq_resume_model}
        remaining=[{'model':model,'shared_tokens':max(0,self.settings.groq_tokens_per_day-shared_by_model.get(model,0)),
            'account_tokens':max(0,self.settings.groq_owner_tokens_per_day-sum(r['tokens'] for r in rows if r['model']==model))} for model in sorted(models)]
        return {'window':'rolling_24_hours','usage':[dict(r) for r in rows],
            'cache_hits':[dict(r) for r in hits],
            'shared_usage':[dict(r) for r in shared],
            'remaining_24h':remaining,
            'limits':{'tokens_per_minute_per_model':self.settings.groq_tokens_per_minute,
                'tokens_per_24h_per_model':self.settings.groq_tokens_per_day,
                'tokens_per_24h_per_account_per_model':self.settings.groq_owner_tokens_per_day,
                'background_tokens_per_24h_per_model':self.background_limit,
                'requests_per_minute_per_model':self.settings.groq_requests_per_minute,
                'requests_per_24h_per_model':self.settings.groq_requests_per_day},
            'provider_capacity':[dict(r) for r in provider],
            'note':'Usage belongs to this account. Provider capacity is shared. Estimated reservations include uncertain or interrupted requests; external Groq usage is only visible through provider headers.'}
