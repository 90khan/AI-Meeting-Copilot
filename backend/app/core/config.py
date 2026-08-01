"""Centralized, typed application configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported application runtime environments."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Validated settings loaded from environment variables and an optional dotenv file.

    The dotenv file is intended for local development only.
    """

    model_config = SettingsConfigDict(
        env_prefix="AI_MEETING_COPILOT_",
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = "sqlite+pysqlite:///./data/app.db"
    speech_to_text_provider: str = "unconfigured"
    translation_provider: str = "unconfigured"
    german_simplification_provider: str = "unconfigured"
    reply_coaching_provider: str = "unconfigured"
    meeting_summarization_provider: str = "unconfigured"

    @field_validator(
        "speech_to_text_provider",
        "translation_provider",
        "german_simplification_provider",
        "reply_coaching_provider",
        "meeting_summarization_provider",
        mode="before",
    )
    @classmethod
    def normalize_provider_name(cls, value: str) -> str:
        """Normalize and validate a configured provider name."""

        normalized_value = value.strip().lower()
        if not normalized_value:
            raise ValueError("Provider name must not be blank.")
        return normalized_value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
