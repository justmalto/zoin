"""Bounded LangGraph workflow. Credentials stay in a closure, never graph state."""
import re
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from services.email_classifier import match_mail_for_job
from services.imap_client import fetch_job_messages


class MailState(TypedDict, total=False):
    application: dict
    messages: list[dict]
    candidates: list[dict]
    matches: list[dict]
    page: dict
    steps: list[dict]


def normalized(value):
    return ' '.join(re.findall(r'\w+', value.casefold()))


def shortlist(application, messages):
    company = normalized(application.get('company') or '')
    role = normalized(application.get('job_title') or '')
    job_id = normalized(application.get('job_id') or '')
    results = []
    for message in messages:
        text = normalized(' '.join(message.get(k, '') for k in ('sender', 'subject', 'body_text')))
        # Phrase boundaries prevent e.g. company "Meta" matching "metadata".
        contains = lambda term: bool(term) and f' {term} ' in f' {text} '
        if contains(job_id) or (contains(company) and (contains(role) or re.search(r'\b(application|interview|assessment|offer|rejected|rejection|candidate|applied)\b', text))):
            results.append(message)
    return results


def run_mail_workflow(application, email_address, password, *, before_uid=None, after_uid=0, limit=5, fetcher=None):
    fetcher = fetcher or fetch_job_messages
    def fetch(state):
        page = fetcher(email_address, password, application, before_uid=before_uid, after_uid=after_uid, limit=limit)
        messages = page.pop('messages')
        return {'messages': messages, 'page': page, 'steps': [{'stage':'job_date_search', 'count':len(messages)}]}
    def deduplicate(state):
        seen = set(); messages = []
        for item in state['messages']:
            key = item.get('message_id') or item['uid']
            if key not in seen:
                messages.append(item); seen.add(key)
        return {'messages':messages, 'steps':state['steps'] + [{'stage':'deduplicate', 'count':len(messages)}]}
    def filter_candidates(state):
        candidates = shortlist(state['application'], state['messages'])
        return {'candidates':candidates, 'steps':state['steps'] + [{'stage':'identity_filter', 'count':len(candidates)}]}
    def classify(state):
        matches = match_mail_for_job(state['application'], state['candidates'])
        by_uid={m['uid']:m for m in state['candidates']}
        matches=[m | {'message_id':by_uid[m['message_uid']].get('message_id',''), 'sender':by_uid[m['message_uid']].get('sender','')[:300], 'subject':by_uid[m['message_uid']].get('subject','')[:500]} for m in matches]
        return {'matches':matches, 'steps':state['steps'] + [{'stage':'groq_classify_and_validate_evidence', 'count':len(matches)}]}
    def review(state):
        matches = state.get('matches', [])
        return {'matches':matches, 'steps':state['steps'] + [{'stage':'user_review', 'count':len(matches)}]}
    graph = StateGraph(MailState)
    for name, node in [('fetch',fetch),('deduplicate',deduplicate),('filter',filter_candidates),('classify',classify),('review',review)]:
        graph.add_node(name,node)
    graph.add_edge(START,'fetch'); graph.add_edge('fetch','deduplicate'); graph.add_edge('deduplicate','filter')
    graph.add_conditional_edges('filter', lambda state: 'classify' if state['candidates'] else 'review')
    graph.add_edge('classify','review'); graph.add_edge('review',END)
    # No checkpointer, tracing callbacks, model tools, automatic writes or retries.
    with tracing_context(enabled=False):
        result = graph.compile().invoke({'application':application}, {'recursion_limit':10, 'callbacks':[]})
    return result['page'] | {'scanned':len(result['messages']), 'matches':result['matches'], 'steps':result['steps'], 'status_updates_applied':0}
