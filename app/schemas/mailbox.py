# app/schemas/mailbox.py

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class MailboxConnect(BaseModel):
    email_address: EmailStr
    app_password: str = Field(min_length=16, max_length=64)

    @field_validator("app_password")
    @classmethod
    def normalize_app_password(cls, value: str) -> str:
        normalized = value.replace(" ", "").strip()

        if len(normalized) != 16:
            raise ValueError("A Google App Password must contain 16 characters")

        return normalized


class MailboxResponse(BaseModel):
    id: UUID
    user_id: UUID
    email_address: EmailStr
    status: str
    last_synced_uid: int
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime


class EmailMessageResponse(BaseModel):
    id: UUID
    mailbox_connection_id: UUID
    sender: str | None
    subject: str | None
    received_at: datetime | None
    classification: str | None
    confidence: float | None
    processed_at: datetime


class EmailApplicationLinkResponse(BaseModel):
    id: UUID
    email_message_id: UUID
    application_id: UUID
    confidence: float = Field(ge=0, le=1)
    reason: str | None