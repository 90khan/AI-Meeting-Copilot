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
    faster_whisper_model: str = "small"
    faster_whisper_device: str = "cpu"
    faster_whisper_compute_type: str = "int8"
    faster_whisper_cpu_threads: int | None = None
    faster_whisper_beam_size: int = 5
    faster_whisper_vad_enabled: bool = False
    faster_whisper_download_directory: Path | None = None

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

    @field_validator("faster_whisper_model", mode="before")
    @classmethod
    def normalize_faster_whisper_model(cls, value: str) -> str:
        """Trim and validate a Faster-Whisper model identifier."""

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Faster-Whisper model must not be blank.")
        return normalized_value

    @field_validator(
        "faster_whisper_device",
        "faster_whisper_compute_type",
        mode="before",
    )
    @classmethod
    def normalize_faster_whisper_runtime_value(cls, value: str) -> str:
        """Normalize and validate a Faster-Whisper runtime value."""

        normalized_value = value.strip().lower()
        if not normalized_value:
            raise ValueError("Faster-Whisper runtime value must not be blank.")
        return normalized_value

    @field_validator("faster_whisper_cpu_threads")
    @classmethod
    def validate_faster_whisper_cpu_threads(cls, value: int | None) -> int | None:
        """Validate the optional Faster-Whisper CPU thread count."""

        if value is not None and value <= 0:
            raise ValueError("Faster-Whisper CPU threads must be greater than zero.")
        return value

    @field_validator("faster_whisper_beam_size")
    @classmethod
    def validate_faster_whisper_beam_size(cls, value: int) -> int:
        """Validate the Faster-Whisper beam-search width."""

        if not 1 <= value <= 10:
            raise ValueError("Faster-Whisper beam size must be between 1 and 10.")
        return value

    @field_validator("faster_whisper_download_directory", mode="before")
    @classmethod
    def normalize_faster_whisper_download_directory(
        cls, value: Path | str | None
    ) -> Path | None:
        """Resolve an optional model download directory without creating it."""

        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return Path(value).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
