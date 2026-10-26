# app/schemas/recommendation.py

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ResumeStrength(BaseModel):
    claim: str
    evidence: list[str] = Field(default_factory=list)


class ResumeGap(BaseModel):
    requirement: str
    severity: str
    explanation: str | None = None


class ResumeRecommendationAnalysis(BaseModel):
    summary: str
    strengths: list[ResumeStrength] = Field(default_factory=list)
    gaps: list[ResumeGap] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ResumeRecommendationResponse(BaseModel):
    id: UUID
    application_id: UUID
    resume_id: UUID
    user_id: UUID
    match_score: float = Field(ge=0, le=100)
    analysis: ResumeRecommendationAnalysis
    model_name: str
    created_at: datetime