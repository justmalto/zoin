from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    supabase_url: str
    supabase_anon_key: str
    supabase_resume_bucket: str = "resumes"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-120b"
    groq_utility_model: str = "openai/gpt-oss-20b"
    groq_resume_model: str = "openai/gpt-oss-20b"
    groq_tokens_per_minute: int = Field(default=7500, ge=1000, le=8000)
    groq_tokens_per_day: int = Field(default=160000, ge=1000, le=200000)
    groq_owner_tokens_per_day: int = Field(default=80000, ge=1000, le=200000)
    groq_background_tokens_per_day: int = Field(default=96000, ge=0, le=200000)
    groq_requests_per_minute: int = Field(default=25, ge=1, le=30)
    groq_requests_per_day: int = Field(default=900, ge=1, le=1000)
    ai_budget_db_path: str = ""

    mail_encryption_key: SecretStr = SecretStr("")
    mail_worker_key: SecretStr = SecretStr("")
    mail_scheduler_enabled: bool = True

    discovery_mock_origin: str = ""
    discovery_enabled: bool = False
    discovery_db_path: str = str(Path(__file__).resolve().parents[2] / '.local' / 'discovery.sqlite3')

    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
