"""Server-only Groq adapter. No SDK dependency, prompt logging, or paid fallback."""
import json
import math
import threading
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from core.config import get_settings

T = TypeVar("T", bound=BaseModel)
ALLOWED_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}
_lock = threading.Lock()
_reservations: list[tuple[float, int]] = []


class AIError(Exception):
    def __init__(self, message: str, status_code: int = 502, retry_after: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


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


def _reserve(estimate: int, budget: int) -> None:
    now = time.monotonic()
    with _lock:
        _reservations[:] = [(t, n) for t, n in _reservations if now - t < 60]
        if estimate > budget:
            raise AIError("Input exceeds the configured free-tier request budget; use a shorter document", 413)
        if sum(n for _, n in _reservations) + estimate > budget or len(_reservations) >= 25:
            retry = max(1, math.ceil(60 - (now - _reservations[0][0])))
            raise AIError("Groq free-tier budget is busy; retry after the cooldown", 429, retry)
        _reservations.append((now, estimate))


def complete_json(system_prompt: str, user_prompt: str, response_model: type[T], *, max_output_tokens: int = 2200) -> T:
    settings = get_settings()
    if not settings.groq_api_key.get_secret_value():
        raise AIError("GROQ_API_KEY is not configured on the server", 503)
    if settings.groq_model not in ALLOWED_MODELS:
        raise AIError("Configure a supported GPT-OSS model", 503)
    schema = _strict_schema(response_model)
    prompt_text = system_prompt + user_prompt + json.dumps(schema)
    # Conservative process-local estimate. Provider limits remain authoritative
    # across app processes and other applications sharing this organization.
    estimate = math.ceil(len(prompt_text.encode("utf-8")) / 3) + max_output_tokens + 100
    _reserve(estimate, settings.groq_tokens_per_minute)
    try:
        response = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
            json={
                "model": settings.groq_model,
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
            raise AIError("Groq free-tier rate limit reached", 429, retry)
        if response.status_code in (401, 403):
            raise AIError("Groq authentication or model access failed; check server configuration", 503)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise AIError("Groq response was incomplete; no analysis was accepted")
        return response_model.model_validate_json(choice["message"]["content"])
    except AIError:
        raise
    except httpx.TimeoutException as exc:
        raise AIError("Groq timed out; retry later", 504) from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
        raise AIError("Groq did not return a complete valid analysis") from exc
