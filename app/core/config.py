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
    groq_tokens_per_minute: int = Field(default=7500, ge=1000, le=8000)

    mail_encryption_key: SecretStr = SecretStr("")
    mail_worker_key: SecretStr = SecretStr("")
    mail_scheduler_enabled: bool = True

    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()