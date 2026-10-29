"""Local discovery preferences; never accepts an owner ID or credentials."""
from typing import Literal
from pydantic import Field
from schemas.ai import StrictModel

class DiscoveryPreferences(StrictModel):
    locations: list[str] = Field(default_factory=lambda: ['India'], max_length=8)
    worldwide_remote: bool = True
    tracks: list[str] = Field(default_factory=list, max_length=12)
    matching_threshold: int = Field(default=80, ge=1, le=100)
    automation_level: Literal['recommend', 'existing_resume', 'review_resume', 'tailored_resume'] = 'recommend'
    max_required_years: int | None = Field(default=None, ge=0, le=40)
    include_senior_roles: bool = False
    daily_enabled: bool = False

    frequency_days: int | None = Field(default=1, ge=1, le=7)
    daily_application_limit: int = Field(default=5, ge=1, le=30)
    paused: bool = False

    found_followup_enabled: bool = False
    found_followup_hours: Literal[12,24,50,72] = 24

    real_applications_enabled: bool = False
