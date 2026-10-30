"""Validated request identity or explicit worker identity; never store a JWT."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_request = ContextVar('ai_request', default=None)
_owner = ContextVar('ai_owner', default=None)
_background = ContextVar('ai_background', default=False)
_cache_allowed = ContextVar('ai_cache_allowed', default=True)


@contextmanager
def temporary_input():
    token=_cache_allowed.set(False)
    try:yield
    finally:_cache_allowed.reset(token)


def cache_allowed(): return _cache_allowed.get()


@contextmanager
def request_scope(request):
    token = _request.set(request)
    try: yield
    finally: _request.reset(token)


@contextmanager
def ai_scope(owner, *, background=None):
    token = _owner.set(str(owner))
    mode = _background.set(_background.get() if background is None else background)
    try: yield
    finally:
        _owner.reset(token); _background.reset(mode)


def identity():
    owner = _owner.get()
    request = _request.get()
    if owner is None and request is not None:
        user = getattr(request.state, 'verified_user', None)
        owner = str(user.id) if user else None
    return owner or '_unattributed', _background.get()


def owned_work(*, background=None):
    def decorate(function):
        @wraps(function)
        def run(owner, *args, **kwargs):
            with ai_scope(owner, background=background):
                return function(owner, *args, **kwargs)
        return run
    return decorate
