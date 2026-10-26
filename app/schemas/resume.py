# app/schemas/resume.py

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ResumeResponse(BaseModel):
    id: UUID
    user_id: UUID
    file_name: str
    file_path: str
    mime_type: str
    content_hash: str
    resume_text: str
    is_active: bool
    created_at: datetime


class ResumeListItem(BaseModel):
    id: UUID
    file_name: str
    mime_type: str
    is_active: bool
    created_at: datetime


class ResumeChunkResponse(BaseModel):
    id: UUID
    resume_id: UUID
    user_id: UUID
    section: str
    chunk_index: int
    content: str
    created_at: datetime