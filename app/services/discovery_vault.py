"""Encrypted local resume snapshots; key stays beside the private DB, never Git."""
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet
from core.config import get_settings


def cipher():
    path=Path(get_settings().discovery_db_path).with_suffix('.key')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:pass
    else:
        with os.fdopen(fd,'wb') as stream:stream.write(Fernet.generate_key())
    os.chmod(path,0o600)
    return Fernet(path.read_bytes())


def seal(resumes):return cipher().encrypt(json.dumps(resumes).encode()).decode()
def open_snapshot(value):return json.loads(cipher().decrypt(value.encode()))
