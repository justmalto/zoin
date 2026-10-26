"""Railway/container entrypoint. Secrets are injected at runtime, never in images."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from cryptography.fernet import Fernet
from core.config import get_settings


def validate_deployment(settings, environment, port):
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535')
    if environment != 'production':
        return
    for name in ('groq_api_key', 'mail_encryption_key', 'mail_worker_key'):
        if not getattr(settings, name).get_secret_value():
            raise ValueError(f'{name.upper()} must be configured on Railway')
    try:
        Fernet(settings.mail_encryption_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise ValueError('MAIL_ENCRYPTION_KEY must be a valid Fernet key') from None
    if len(settings.mail_worker_key.get_secret_value()) < 40:
        raise ValueError('MAIL_WORKER_KEY must contain at least 40 characters')
    from urllib.parse import urlsplit
    if not settings.allowed_origins or any(
        urlsplit(origin).scheme != 'https' or not urlsplit(origin).hostname
        or '*' in origin or urlsplit(origin).path not in ('', '/')
        for origin in settings.allowed_origins
    ):
        raise ValueError('ALLOWED_ORIGINS must list the exact HTTPS Vercel frontend origins')
    if not settings.mail_scheduler_enabled:
        raise ValueError('MAIL_SCHEDULER_ENABLED must be true for the always-on deployment')


def main():
    settings = get_settings()
    try:
        port = int(os.environ.get('PORT', '8000'))
        validate_deployment(settings, os.environ.get('ZOIN_ENVIRONMENT', 'development'), port)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if os.environ.get('ZOIN_ENVIRONMENT') == 'production':
        # Validate migration/worker capability before reporting this deployment healthy.
        from services.mail_sync import worker
        try:
            worker('due')
        except Exception:
            raise SystemExit('Mail worker database access failed. Check migrations and the existing MAIL_WORKER_KEY.') from None
    os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'main:app', '--app-dir', str(ROOT / 'app'),
                            '--host', '0.0.0.0', '--port', str(port), '--workers', '1',
                            '--timeout-graceful-shutdown', '30'])


if __name__ == '__main__':
    main()
