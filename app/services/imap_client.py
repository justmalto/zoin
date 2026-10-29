# app/services/imap_client.py

import email
import imaplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from html.parser import HTMLParser


GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993


class ImapError(Exception):
    pass


@dataclass
class ImapMessage:
    uid: int
    sender: str
    subject: str
    body_text: str
    received_at: datetime


def verify_gmail_connection(
    email_address: str,
    app_password: str,
) -> None:
    client = _connect(email_address, app_password)

    try:
        status, _ = client.select("INBOX", readonly=True)

        if status != "OK":
            raise ImapError("Unable to read Gmail inbox")

    finally:
        _close(client)


def fetch_messages_after_uid(
    email_address: str,
    app_password: str,
    after_uid: int,
    limit: int = 50,
) -> list[ImapMessage]:
    client = _connect(email_address, app_password)

    try:
        status, _ = client.select("INBOX", readonly=True)

        if status != "OK":
            raise ImapError("Unable to read Gmail inbox")

        status, raw_uids = client.uid(
            "search",
            None,
            f"UID {after_uid + 1}:*",
        )

        if status != "OK":
            raise ImapError("Unable to search Gmail inbox")

        # IMAP ranges can return the last existing UID when the lower bound
        # exceeds it. Filter explicitly and never skip the older pending batch.
        if not 1 <= limit <= 50 or after_uid < 0:
            raise ImapError("Invalid IMAP batch bounds")
        uids = sorted({int(uid) for uid in (raw_uids[0] or b"").split() if int(uid) > after_uid})[:limit]
        messages: list[ImapMessage] = []

        for raw_uid in uids:
            uid = int(raw_uid)

            status, raw_message = client.uid(
                "fetch",
                str(uid),
                "(BODY.PEEK[])",
            )

            if status != "OK" or not raw_message:
                raise ImapError("Unable to fetch the complete pending batch")

            message_bytes = raw_message[0][1]
            parsed = email.message_from_bytes(message_bytes)

            messages.append(
                ImapMessage(
                    uid=uid,
                    sender=_decode_header(parsed.get("From", "")),
                    subject=_decode_header(parsed.get("Subject", "")),
                    body_text=_extract_body(parsed),
                    received_at=_parse_date(parsed.get("Date")),
                )
            )

        return messages

    finally:
        _close(client)


def _connect(
    email_address: str,
    app_password: str,
) -> imaplib.IMAP4_SSL:
    try:
        client = imaplib.IMAP4_SSL(
            GMAIL_IMAP_HOST,
            GMAIL_IMAP_PORT,
            timeout=20,
        )

        status, _ = client.login(
            email_address,
            app_password,
        )

        if status != "OK":
            raise ImapError("Gmail login failed")

        return client

    except imaplib.IMAP4.error as exc:
        raise ImapError(
            "Unable to authenticate with Gmail IMAP"
        ) from exc


def _close(client: imaplib.IMAP4_SSL) -> None:
    try:
        client.logout()
    except Exception:
        pass


def _decode_header(value: str) -> str:
    decoded_parts = decode_header(value)
    output = []

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            output.append(
                part.decode(encoding or "utf-8", errors="replace")
            )
        else:
            output.append(part)

    return "".join(output).strip()


def _extract_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(
                part.get("Content-Disposition", "")
            )

            if (
                content_type == "text/plain"
                and "attachment" not in disposition
            ):
                payload = part.get_payload(
                    decode=True
                ) or b""

                return payload.decode(
                    part.get_content_charset() or "utf-8",
                    errors="replace",
                ).strip()

        for part in message.walk():
            if part.get_content_type() == "text/html" and "attachment" not in str(part.get("Content-Disposition", "")):
                return _html_text((part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace"))
        return ""

    if message.get_content_type() == "text/html":
        return _html_text((message.get_payload(decode=True) or b"").decode(message.get_content_charset() or "utf-8", errors="replace"))
    if message.get_content_type() != "text/plain":
        return ""

    payload = message.get_payload(decode=True) or b""

    return payload.decode(
        message.get_content_charset() or "utf-8",
        errors="replace",
    ).strip()


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    try:
        parsed = email.utils.parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)

        return parsed

    except (TypeError, ValueError):
        return datetime.now(timezone.utc)

def _html_text(value: str) -> str:
    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self.hidden = 0
        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.hidden += 1
        def handle_endtag(self, tag):
            if tag in {"script", "style"} and self.hidden:
                self.hidden -= 1
        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)
    parser = TextParser()
    parser.feed(value)
    return " ".join(parser.parts).strip()


def fetch_job_messages(email_address, app_password, application, *, before_uid=None, after_uid=0, limit=5):
    """Recent, server-filtered candidate page; bounded PEEK preserves unread flags."""
    import re
    from datetime import timedelta
    if not 1 <= limit <= 5 or after_uid < 0 or (before_uid is not None and before_uid < 1):
        raise ImapError('Invalid mailbox page')
    terms = []
    for key in ('job_id', 'company', 'job_title'):
        term = re.sub(r'[^\x20-\x7e]', ' ', application.get(key) or '').strip()[:160]
        if term and term not in terms:
            terms.append(term)
    if not terms:
        raise ImapError('A searchable job identity is required')
    quote = lambda text: '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    clauses = ['TEXT ' + quote(term) for term in terms]
    expression = clauses[-1]
    for clause in reversed(clauses[:-1]):
        expression = f'OR ({clause}) ({expression})'
    since = datetime.now(timezone.utc) - timedelta(days=180)
    submitted = application.get('applied_at') or application.get('submitted_at')
    if submitted:
        try:
            applied = datetime.fromisoformat(submitted.replace('Z','+00:00'))
            since = max(datetime.now(timezone.utc)-timedelta(days=365), applied-timedelta(days=14))
        except (ValueError, TypeError):
            pass
    # Fixed English month abbreviations, independent of host locale.
    month = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')[since.month-1]
    upper = str(before_uid - 1) if before_uid is not None else '*'
    if before_uid is not None and before_uid - 1 <= after_uid:
        return {'messages':[], 'next_before_uid':None, 'has_more':False, 'candidate_count':0}
    criteria = f'(SINCE {since.day:02d}-{month}-{since.year} UID {after_uid+1}:{upper} {expression})'
    client = _connect(email_address, app_password)
    try:
        status, _ = client.select('INBOX', readonly=True)
        if status != 'OK': raise ImapError('Unable to read Gmail inbox')
        status, data = client.uid('search', None, criteria)
        if status != 'OK': raise ImapError('Unable to search job mail')
        uids = sorted({int(uid) for uid in (data[0] or b'').split() if int(uid)>after_uid and (before_uid is None or int(uid)<before_uid)}, reverse=True)
        messages = []
        for uid in uids[:limit]:
            status, raw = client.uid('fetch',str(uid),'(BODY.PEEK[]<0.65536>)')
            parts = [part[1] for part in (raw or []) if isinstance(part,tuple) and isinstance(part[1],bytes)]
            if status != 'OK' or not parts: raise ImapError('Incomplete job email batch; retry this page')
            parsed = email.message_from_bytes(parts[0])
            try:
                stamp = email.utils.parsedate_to_datetime(parsed.get('Date', ''))
                received_at = stamp.isoformat() if stamp.tzinfo is not None else None
            except (ValueError, TypeError, IndexError):
                received_at = None
            messages.append({'uid':uid, 'message_id':str(parsed.get('Message-ID',''))[:500],
                             'received_at':received_at,
                             'sender':_decode_header(parsed.get('From',''))[:500],
                             'subject':_decode_header(parsed.get('Subject',''))[:500],
                             'body_text':_extract_body(parsed)[:8000]})
        return {'messages':messages, 'next_before_uid':uids[min(limit,len(uids))-1] if len(uids)>limit else None,
                'has_more':len(uids)>limit, 'candidate_count':len(uids)}
    finally:
        _close(client)
