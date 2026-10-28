"""Validated contracts for model output and authenticated analysis endpoints."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, EmailStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobExtraction(StrictModel):
    job_title: str | None
    company: str | None
    location: str | None
    job_id: str | None
    employment_type: str | None
    minimum_experience_years: float | None = Field(ge=0, le=80)
    skills: list[str]
    education: list[str]
    responsibilities: list[str]


Category = Literal["ml_modeling", "data_preparation", "validation", "mlops_api_cloud", "business_domain", "experience", "education", "communication"]


class Criterion(StrictModel):
    category: Category
    requirement_id: str | None
    coverage: Literal["met", "partial", "not_evidenced", "not_required"]
    resume_evidence_ids: list[str]
    explanation: str


class Strength(StrictModel):
    claim: str
    evidence_ids: list[str]


class ResumeAssessment(StrictModel):
    summary: str
    criteria: list[Criterion]
    strengths: list[Strength]
    suggestions: list[str]


class EmailClassificationOutput(StrictModel):
    category: Literal["confirmation", "assessment", "interview", "rejection", "offer", "other"]
    application_status: Literal["applied", "assessment", "interview", "rejected", "offer"] | None
    confidence: float = Field(ge=0, le=1)
    reason: str


class ApplicationMatchOutput(StrictModel):
    application_id: str | None
    confidence: float = Field(ge=0, le=1)
    reason: str


class MailMatch(StrictModel):
    message_uid: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    category: Literal["confirmation", "assessment", "interview", "rejection", "offer", "other"]
    suggested_status: Literal["applied", "assessment", "interview", "rejected", "offer"] | None
    evidence_ids: list[str]
    reason: str
    company: str | None
    job_title: str | None
    job_id: str | None
    interview_details: str | None
    deadline_text: str | None


class MailMatches(StrictModel):
    matches: list[MailMatch]


class JobExtractRequest(StrictModel):
    text: str = Field(min_length=20, max_length=18000)


class ResumeMatchRequest(StrictModel):
    job_description: str = Field(min_length=20, max_length=18000)
    resume_id: UUID


class MailSearchRequest(StrictModel):
    email_address: EmailStr
    app_password: SecretStr
    before_uid: int | None = Field(default=None, ge=1)
    after_uid: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=5)


class JobLinkRequest(StrictModel):
    url: str = Field(min_length=1, max_length=2000)
