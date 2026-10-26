"""Cron entry point: run once per minute with the project's .venv Python.

No credentials are accepted as CLI arguments. Configuration comes from .env or
server environment. MAIL_SCHEDULER_ENABLED=false avoids an extra API worker.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from services.mail_sync import configured,worker,sync_account
if __name__=='__main__':
    if not configured():raise SystemExit('Email sync server keys are not configured.')
    worker('cleanup')
    for user_id in worker('due'):sync_account(user_id,'scheduled')
