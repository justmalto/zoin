from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, SecretStr
from core.auth import CurrentUser
from services.imap_client import verify_gmail_connection, ImapError
from services.groq_client import AIError

router=APIRouter(prefix='/mailboxes',tags=['mailboxes'])


class MailboxVerify(BaseModel):
    email_address: EmailStr
    app_password: SecretStr


@router.post('/verify')
def verify_mailbox(payload: MailboxVerify, user_id: CurrentUser):
    password=''.join(payload.app_password.get_secret_value().split())
    if len(password)!=16:raise AIError('A Gmail App Password must contain 16 characters',422)
    try:verify_gmail_connection(str(payload.email_address),password)
    except (ImapError,OSError) as exc:raise AIError('Unable to connect to Gmail. Check the address and App Password.',502) from exc
    return {'email_address':str(payload.email_address),'verified':True,'persistence':'session-only'}
