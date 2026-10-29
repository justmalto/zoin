"""Profession-independent agent contracts with source-bound evidence."""
from typing import Literal
from pydantic import EmailStr, Field
from schemas.ai import StrictModel

class CareerDirection(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    role_titles: list[str] = Field(min_length=1, max_length=5)
    evidence_ids: list[str] = Field(min_length=1, max_length=128)

class ProfileSkill(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    evidence_ids: list[str] = Field(min_length=1, max_length=128)

class CareerProfile(StrictModel):
    directions: list[CareerDirection] = Field(min_length=1, max_length=4)
    skills: list[ProfileSkill] = Field(min_length=1, max_length=30)

class Requirement(StrictModel):
    id: str
    description: str = Field(max_length=500)
    job_evidence_ids: list[str] = Field(min_length=1, max_length=4)
    mandatory: bool
    weight: int = Field(ge=1, le=3)

class JobRequirements(StrictModel):
    requirements: list[Requirement] = Field(min_length=1, max_length=24)

class RequirementCoverage(StrictModel):
    requirement_id: str
    coverage: Literal['met','partial','not_evidenced','conflicting']
    resume_evidence_ids: list[str] = Field(max_length=5)
    explanation: str = Field(max_length=500)

class GeneralAssessment(StrictModel):
    criteria: list[RequirementCoverage] = Field(min_length=1, max_length=24)
    suggestions: list[str] = Field(max_length=8)

class AgentApplicationRequest(StrictModel):
    job_url: str = Field(min_length=1, max_length=2000)
    run_id: str = Field(min_length=1, max_length=100)
    applicant_name: str = Field(min_length=1, max_length=150)
    applicant_email: EmailStr

class ResumeEdit(StrictModel):
    source_id: str
    replacement: str = Field(min_length=1,max_length=1000)
    explanation: str = Field(min_length=1,max_length=500)

class TailoringProposal(StrictModel):
    edits: list[ResumeEdit] = Field(max_length=8)
    unaddressed_gaps: list[str] = Field(max_length=8)

class ResumeDraftRequest(StrictModel):
    job_url: str = Field(min_length=1,max_length=2000)
    run_id: str = Field(min_length=1,max_length=100)
