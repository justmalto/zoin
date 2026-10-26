from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApplicationStatus(str, Enum):
    found='found'; draft='draft'; applied='applied'; assessment='assessment'; interview='interview'
    offer='offer'; rejected='rejected'; withdrawn='withdrawn'; failed='failed'


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    company: str = Field(min_length=1,max_length=300)
    job_title: str = Field(min_length=1,max_length=300)
    job_description: str = Field(min_length=20,max_length=18000)
    job_id: str | None = Field(default=None,max_length=300)
    application_url: str | None = Field(default=None,max_length=2000)
    location: str | None = Field(default=None,max_length=300)
    job_type: str | None = Field(default=None,max_length=100)
    selected_resume_id: UUID | None = None
    cover_letter: str | None = None
    submitted_at: datetime | None = None
    initial_status: Literal['found'] = 'found'


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    company: str | None = Field(default=None,min_length=1,max_length=300)
    job_title: str | None = Field(default=None,min_length=1,max_length=300)
    job_description: str | None = Field(default=None,min_length=20,max_length=18000)
    job_id: str | None = Field(default=None,max_length=300)
    application_url: str | None = Field(default=None,max_length=2000)
    location: str | None = Field(default=None,max_length=300)
    job_type: str | None = Field(default=None,max_length=100)
    selected_resume_id: UUID | None = None
    cover_letter: str | None = None
    submitted_at: datetime | None = None

    @field_validator('company','job_title','job_description')
    @classmethod
    def required_fields_cannot_be_null(cls,value):
        if value is None: raise ValueError('Required fields cannot be cleared')
        return value


class StatusTransition(BaseModel):
    to_status: ApplicationStatus
    note: str | None = Field(default=None,max_length=5000)
