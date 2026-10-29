"""Resume-first discovery over public employer boards. No browser or submission."""
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from services.job_link import fetch_posting, clean_html, clean_description
from services.groq_client import AIError

# Operator-owned source allowlist. Arbitrary client URLs cannot become fetch targets.
SOURCES = [('greenhouse', 'sportygroup'), ('lever', 'sambatv'), ('lever', 'meesho')]


def contains(text, term):
    return bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I))


def canonical(url):
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return None
        return urlunsplit(('https', p.netloc.lower(), p.path.rstrip('/'), '', ''))
    except ValueError:
        return None


def profile_resumes(resumes, *, cache=None, caller=None):
    from services.career_agent import build_profile
    return [build_profile(r,cache=cache,caller=caller) for r in resumes[:10] if len((r.get('resume_text') or '').strip())>=20]


def search_plan(profiles, prefs):
    plans = {}
    for profile in profiles:
        for direction in profile['directions']:
            key = direction['name'].casefold()
            if prefs.get('tracks') and key not in [t.casefold() for t in prefs['tracks']]:
                continue
            plan = plans.setdefault(key, {'track':key, 'label':direction['name'], 'roles':[], 'resume_ids':[], 'skills':[]})
            plan['resume_ids'].append(profile['id'])
            plan['roles'] = list(dict.fromkeys(plan['roles'] + direction['role_titles']))
            plan['skills'] = list(dict.fromkeys(plan['skills'] + profile['skills']))
    for plan in plans.values():
        plan['queries'] = [role+' '+' '.join(plan['skills'][:3])+' '+', '.join(prefs['locations']) for role in plan['roles'][:3]]
    return list(plans.values())


def track_for_title(title, plans):
    title_words=set(re.findall(r'\w+',title.casefold()))
    best=(0,None)
    for plan in plans:
        for role in plan['roles']:
            words=set(re.findall(r'\w+',role.casefold()))-{'senior','junior','lead','principal','staff','ii','iii'}
            overlap=len(words & title_words)
            if words and overlap>=min(2,len(words)):
                score=overlap/len(words)
                if score>best[0]:best=(score,plan['track'])
    return best[1]


def location_allowed(location, prefs):
    value = location.casefold()
    if not prefs['locations']:
        return True
    if any(contains(value, loc) for loc in prefs['locations']):
        return True
    if any(loc.casefold() == 'india' for loc in prefs['locations']) and any(contains(value, city) for city in ('bengaluru', 'bangalore', 'mumbai', 'pune', 'hyderabad', 'gurugram', 'gurgaon', 'noida', 'new delhi', 'chennai', 'kolkata', 'ahmedabad', 'kochi')):
        return True
    # Remote does not imply permission to work from any country.
    return prefs['worldwide_remote'] and bool(re.search(r'\b(?:worldwide|anywhere|global)\b', value)) and bool(re.search(r'\bremote\b', value))


def fetch_source(source):
    kind, board = source
    url = f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs' if kind == 'greenhouse' else f'https://api.lever.co/v0/postings/{board}?mode=json&limit=100'
    raw, _ = fetch_posting(url, json_response=True)
    data = json.loads(raw)
    jobs = data.get('jobs', []) if kind == 'greenhouse' and isinstance(data, dict) else data
    if not isinstance(jobs, list):
        raise ValueError('Invalid source response')
    results = []
    for item in jobs[:500]:
        if not isinstance(item, dict):
            continue
        categories = item.get('categories') or {}
        location = (item.get('location') or {}).get('name', '') if kind == 'greenhouse' else categories.get('location', '')
        link = item.get('absolute_url') if kind == 'greenhouse' else item.get('hostedUrl')
        if not isinstance(link, str) or not canonical(link):
            continue
        results.append({'source': kind, 'board': board, 'id': str(item.get('id', ''))[:100], 'url': link,
                        'title': str(item.get('title') if kind == 'greenhouse' else item.get('text', ''))[:300],
                        'company': {'sportygroup': 'Sporty Group', 'sambatv': 'Samba TV', 'meesho': 'Meesho'}.get(board, board), 'location': str(location)[:300],
                        # Greenhouse updated_at is an update time, not a posting date.
                        'source_updated_at': item.get('updated_at'), 'description': '',
                        '_body': item if kind == 'lever' else None})
    return results


def read_description(job):
    if job['source'] == 'greenhouse':
        url = f"https://boards-api.greenhouse.io/v1/boards/{job['board']}/jobs/{job['id']}"
        raw, _ = fetch_posting(url, json_response=True)
        item = json.loads(raw)
        if str(item.get('id')) != job['id']:
            raise ValueError('Job identity changed')
        description = item.get('content') or ''
    else:
        item = job['_body']
        description = (item.get('descriptionPlain') or item.get('description') or '') + '\n' + '\n'.join(str(x.get('text','')) + '\n' + str(x.get('content','')) for x in item.get('lists', []) if isinstance(x, dict)) + '\n' + str(item.get('additionalPlain') or '')
    text = clean_description(clean_html(unescape(description)))[:18000]
    if len(text) < 80:
        raise ValueError('No readable description')
    return {k: v for k, v in job.items() if k != '_body'} | {'description': text}


def senior_allowed(title, prefs):
    return prefs.get('include_senior_roles', False) or not re.search(r'\b(?:senior|sr\.?|staff|principal|lead|head|director|manager|iii|iv)\b', title, re.I)


def rank_job(job, profiles, prefs):
    plans=search_plan(profiles,prefs)
    track=track_for_title(job['title'],plans)
    if not track or not location_allowed(job['location'],prefs) or not senior_allowed(job['title'],prefs):return None
    text=job['title']+'\n'+job['description']
    required_years=re.findall(r'\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b',text,re.I)
    if prefs.get('max_required_years') is not None and required_years and max(map(int,required_years))>prefs['max_required_years']:return None
    plan=next(p for p in plans if p['track']==track)
    all_skills=list(dict.fromkeys(s for p in profiles for s in p['skills']))
    job_skills=[skill for skill in all_skills if contains(text,skill)]
    choices=[]
    for profile in profiles:
        if profile['id'] not in plan['resume_ids']:continue
        shared=[skill for skill in job_skills if skill in profile['skills']]
        if shared:choices.append((len(shared),profile,shared))
    if not choices:return None
    _,resume,shared=max(choices,key=lambda c:(c[0],c[1]['id']))
    return job|{'track':track,'resume_id':resume['id'],'resume_name':resume['name'],'resume_hash':resume['hash'],
               'skill_coverage':round(100*len(shared)/max(1,len(job_skills))), 'matched_skills':shared,
               'gaps':[], 'why':'Career direction and evidence-supported skills match the posting.',
               'score_kind':'Preliminary retrieval only; AI threshold assessment required',
               'checked_at':datetime.now(timezone.utc).isoformat()}


def qualify_job(job, profiles, resumes, prefs, *, cache=None, assessor=None):
    from services.career_agent import assess, requirements_for
    requirements=requirements_for(job['description'],cache=cache) if assessor is None else None
    candidates=[]
    # A retrieval profile does not preselect the application resume. Compare every saved resume.
    for resume in resumes[:10]:
        matching=next((p for p in profiles if p['id']==str(resume['id'])),None)
        if not matching:continue
        result=(assessor(job['description'],resume['resume_text'],prefs['matching_threshold']) if assessor else
                assess(job['description'],resume['resume_text'],requirements,threshold=prefs['matching_threshold'],cache=cache))
        candidates.append((result['score'],matching,result))
    if not candidates:return None
    eligible=[c for c in candidates if c[2]['eligible_for_automation']]
    score,profile,result=max(eligible or candidates,key=lambda c:(c[0],c[1]['id']))
    if not result['threshold_met']:return None
    return job|{'match_score':score,'resume_id':profile['id'],'resume_name':profile['name'],'resume_hash':profile['hash'],
                'assessment':result, 'score_kind':'Evidence-backed requirement coverage, not hiring probability',
                'automation_ready':result['eligible_for_automation']}


def balanced_top_four(ranked, plans):
    buckets = {p['track']: sorted([j for j in ranked if j['track'] == p['track']], key=lambda j: (-j.get('match_score',j['skill_coverage']), j['url'])) for p in plans}
    result, seen = [], set()
    while len(result) < 4:
        added = False
        for bucket in buckets.values():
            while bucket and canonical(bucket[0]['url']) in seen:
                bucket.pop(0)
            if bucket and len(result) < 4:
                job = bucket.pop(0); result.append(job); seen.add(canonical(job['url'])); added = True
        if not added:
            break
    return result


class DiscoveryState(TypedDict, total=False):
    plans: list
    candidates: list
    ranked: list
    jobs: list
    warnings: list
    steps: list


def discover(profiles, prefs, excluded=(), *, source_fetcher=None, detail_fetcher=None, resumes=None, cache=None, assessor=None):
    source_fetcher = source_fetcher or fetch_source
    detail_fetcher = detail_fetcher or read_description
    deadline = time.monotonic() + 120
    excluded = {canonical(url) for url in excluded if isinstance(url, str)}
    def plan(state):
        plans = search_plan(profiles, prefs)
        return {'plans': plans, 'steps': [{'stage': 'resume_first_profiles', 'count': len(plans)}], 'warnings': []}
    def search(state):
        if not state['plans']:
            return {'candidates': [], 'warnings': ['No enabled career track was supported by the saved resumes.']}
        warnings, candidates, seen = [], [], set()
        # Two outbound connections maximum; bounded static sources, no resume sent.
        def safe(source):
            try:
                if time.monotonic() >= deadline: raise ValueError('Deadline')
                return source_fetcher(source), None
            except (AIError, ValueError, TypeError, KeyError): return [], f'{source[0]}/{source[1]} could not be read; coverage is partial.'
        with ThreadPoolExecutor(max_workers=2) as pool:
            for jobs, error in pool.map(safe, SOURCES):
                if error: warnings.append(error)
                for job in jobs:
                    key = canonical(job['url'])
                    if key in seen or key in excluded or not key:
                        continue
                    track = track_for_title(job['title'],state['plans'])
                    if track not in [p['track'] for p in state['plans']] or not location_allowed(job['location'], prefs) or not senior_allowed(job['title'], prefs):
                        continue
                    seen.add(key); candidates.append(job)
        # Read a balanced bounded sample, rather than consuming every JD or favoring one track.
        selected = []
        for p in state['plans']:
            bucket = [j for j in candidates if track_for_title(j['title'],state['plans']) == p['track']]
            bucket.sort(key=lambda j: (-sum(contains(j['title'], s) for s in p['skills']), j['url']))
            selected.extend(bucket[:6])
        return {'candidates': selected, 'warnings': warnings, 'steps': state['steps'] + [{'stage': 'targeted_public_boards', 'count': len(candidates)}]}
    def assess(state):
        ranked, warnings = [], list(state['warnings'])
        for candidate in state['candidates'][:12]:
            if time.monotonic() >= deadline:
                warnings.append('Search time budget reached; remaining candidates were skipped.')
                break
            try:
                job = detail_fetcher(candidate)
                ranked_job = rank_job(job, profiles, prefs)
                if ranked_job:
                    qualified=qualify_job(ranked_job,profiles,resumes or [],prefs,cache=cache,assessor=assessor)
                    if qualified:ranked.append(qualified)
            except AIError:
                raise  # Quota/error states must never masquerade as an empty successful shortlist.
            except (ValueError, TypeError, KeyError):
                warnings.append('A candidate description was unavailable and was skipped.')
        return {'ranked': ranked, 'warnings': warnings, 'steps': state['steps'] + [{'stage': 'clean_and_evidence_rank', 'count': len(ranked)}]}
    def select(state):
        jobs = balanced_top_four(state['ranked'], state['plans'])
        return {'jobs': jobs, 'steps': state['steps'] + [{'stage': 'balanced_shortlist', 'count': len(jobs)}]}
    graph = StateGraph(DiscoveryState)
    for name, node in [('plan', plan), ('search', search), ('assess', assess), ('select', select)]: graph.add_node(name, node)
    graph.add_edge(START, 'plan'); graph.add_edge('plan', 'search'); graph.add_edge('search', 'assess'); graph.add_edge('assess', 'select'); graph.add_edge('select', END)
    with tracing_context(enabled=False):
        result = graph.compile().invoke({}, {'recursion_limit': 8, 'callbacks': []})
    return {'jobs': result['jobs'], 'plans': result['plans'], 'warnings': result['warnings'], 'steps': result['steps'],
            'coverage': 'Configured public Greenhouse and Lever employer boards only; not an internet-wide search.',
            'assessment': 'Profession-independent Groq requirement assessment; all saved resumes considered against the matching threshold.'}
