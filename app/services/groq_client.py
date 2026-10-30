"""Server-only Groq adapter. No SDK dependency, prompt logging, or paid fallback."""
import json
import math
import hashlib
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from core.config import get_settings
from services.ai_budget import Budget, BudgetBlocked
from services.ai_context import identity, cache_allowed

T = TypeVar("T", bound=BaseModel)
ALLOWED_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}


class AIError(Exception):
    def __init__(self, message: str, status_code: int = 502, retry_after: int | None = None, code: str = 'ai_error'):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.code = code


# Task selection is explicit and deterministic; no provider/model fallback on errors.
TASKS = {'TailoringProposal': ('resume_draft','resume',2000),
         'CareerProfile': ('resume_profile','utility',2200),
         'JobExtraction': ('job_extract','utility',1200),
         'MailMatches': ('mail_match','utility',1800),
         'EmailClassificationOutput': ('mail_classify','utility',500),
         'ApplicationMatchOutput': ('mail_link','utility',500),
         'FactualVerification': ('resume_verify','primary',400),
         'JobRequirements': ('job_requirements','primary',2200),
         'GeneralAssessment': ('agent_match','primary',2200),
         'ResumeAssessment': ('resume_match','primary',1800)}


def model_for(model, settings=None):
    s=settings or get_settings()
    kind=TASKS.get(model.__name__, ('general','primary',2200))[1]
    return {'primary':s.groq_model,'utility':s.groq_utility_model,'resume':s.groq_resume_model}[kind]


def cached_result(task):
    """Cache only after the service's evidence/factual validation has succeeded."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            owner,_=identity()
            if owner=='_unattributed' or not cache_allowed(): return function(*args,**kwargs)
            s=get_settings()
            selected={'resume_match':s.groq_model,'job_extract':s.groq_utility_model,'mail_match':s.groq_utility_model}.get(task,
                [s.groq_model,s.groq_utility_model,s.groq_resume_model])
            material=json.dumps([task,'validated-v1',selected,
                datetime.now(timezone.utc).date().isoformat() if task=='resume_match' else None,args,kwargs],sort_keys=True,ensure_ascii=False)
            key=hashlib.sha256(material.encode()).hexdigest()
            budget=Budget(s); value=budget.cached(owner,key)
            if value is not None:
                budget.cache_hit(owner,task);return value
            try: lease=budget.claim_cache(owner,key)
            except BudgetBlocked as exc:raise AIError(exc.message,429,exc.retry_after,exc.code) from None
            try:
                value=budget.cached(owner,key)
                if value is not None:
                    budget.cache_hit(owner,task);return value
                value=function(*args,**kwargs)
                budget.cached(owner,key,value)
                return value
            finally:budget.release_cache(owner,key,lease)
        return run
    return decorate


def _strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    def visit(node):
        if isinstance(node, dict):
            # Enforce locally even when the provider supports a smaller JSON subset.
            for key in ("default", "minimum", "maximum", "minLength", "maxLength", "format"):
                node.pop(key, None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in list(node.values()):
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema


def complete_json(system_prompt: str, user_prompt: str, response_model: type[T], *, max_output_tokens: int = 2200) -> T:
    settings = get_settings()
    if not settings.groq_api_key.get_secret_value():
        raise AIError("GROQ_API_KEY is not configured on the server", 503)
    selected=model_for(response_model,settings)
    if selected not in ALLOWED_MODELS:
        raise AIError("Configure a supported GPT-OSS model", 503)
    schema = _strict_schema(response_model)
    task,_,output_limit=TASKS.get(response_model.__name__,('general','primary',2200))
    max_output_tokens=min(max_output_tokens,output_limit)
    prompt_text = system_prompt + user_prompt + json.dumps(schema)
    # Conservative estimate reserved atomically in the persistent ledger.
    # Provider limits remain authoritative for other applications sharing the organization.
    estimate = math.ceil(len(prompt_text.encode("utf-8")) / 3) + max_output_tokens + 100
    budget=Budget(settings); owner,background=identity()
    try: reservation=budget.reserve(selected,owner,task,estimate,background)
    except BudgetBlocked as exc:
        raise AIError(exc.message,413 if exc.code=='request_too_large' else 429,exc.retry_after,exc.code) from None
    except (OSError,sqlite3.Error):
        raise AIError('AI budget storage is unavailable. No AI request was sent; check server storage.',503,code='budget_unavailable') from None
    finished=False
    try:
        response = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
            json={
                "model": selected,
                "temperature": 0,
                "reasoning_effort": "low",
                "max_completion_tokens": max_output_tokens,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": response_model.__name__, "strict": True, "schema": schema,
                }},
                "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            },
            timeout=httpx.Timeout(60, connect=10),
        )
        if response.status_code == 429:
            try:
                retry = max(1, min(86400, math.ceil(float(response.headers.get("retry-after", "60")))))
            except (ValueError, OverflowError):
                retry = 60
            budget.finish(reservation,status='rejected',headers=response.headers,retry_after=retry);finished=True
            raise AIError("Groq free-tier rate limit reached for "+selected+"; retry after the indicated cooldown", 429, retry,'provider_rate_limit')
        if response.status_code in (401, 403):
            budget.finish(reservation,status='rejected',headers=response.headers);finished=True
            raise AIError("Groq authentication or model access failed; check server configuration", 503)
        response.raise_for_status()
        payload=response.json()
        if not isinstance(payload,dict):raise AIError('Groq returned an invalid response envelope')
        budget.finish(reservation,usage=payload.get('usage'),headers=response.headers);finished=True
        choice = payload["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise AIError("Groq response was incomplete; no analysis was accepted")
        return response_model.model_validate_json(choice["message"]["content"])
    except AIError:
        raise
    except httpx.TimeoutException as exc:
        raise AIError("Groq timed out; retry later", 504) from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
        raise AIError("Groq did not return a complete valid analysis") from exc
    finally:
        if not finished:budget.finish(reservation,status='uncertain')
