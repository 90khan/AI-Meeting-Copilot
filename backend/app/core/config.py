"""Centralized, typed application configuration."""

import math
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
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
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_translation_model: str = "qwen2.5:3b"
    ollama_german_simplification_model: str = "qwen2.5:3b"
    ollama_reply_coaching_model: str = "qwen2.5:3b"
    ollama_meeting_summarization_model: str = "qwen2.5:3b"
    ollama_request_timeout_seconds: float = 120.0
    ollama_temperature: float = 0.1
    ollama_context_length: int = 8192
    ollama_keep_alive: str = "5m"
    sidecar_auth_token: str | None = Field(default=None, repr=False)
    sidecar_host: str = "127.0.0.1"
    sidecar_port: int = 0
    sidecar_startup_timeout_seconds: float = 15.0

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

    @field_validator("ollama_base_url", mode="before")
    @classmethod
    def normalize_ollama_base_url(cls, value: str) -> str:
        """Normalize and validate the Ollama server base URL."""

        normalized_value = value.strip().rstrip("/")
        parsed_url = urlparse(normalized_value)
        if (
            not normalized_value
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
        ):
            raise ValueError("Ollama base URL must use http or https.")
        return normalized_value

    @field_validator(
        "ollama_translation_model",
        "ollama_german_simplification_model",
        "ollama_reply_coaching_model",
        "ollama_meeting_summarization_model",
        mode="before",
    )
    @classmethod
    def normalize_ollama_model_name(cls, value: str) -> str:
        """Trim and validate an Ollama model identifier."""

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Ollama model name must not be blank.")
        return normalized_value

    @field_validator("ollama_request_timeout_seconds")
    @classmethod
    def validate_ollama_request_timeout_seconds(cls, value: float) -> float:
        """Validate the finite positive Ollama request timeout."""

        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                "Ollama request timeout must be finite and greater than zero."
            )
        return value

    @field_validator("ollama_temperature")
    @classmethod
    def validate_ollama_temperature(cls, value: float) -> float:
        """Validate the finite Ollama generation temperature."""

        if not math.isfinite(value) or not 0.0 <= value <= 2.0:
            raise ValueError("Ollama temperature must be between 0.0 and 2.0.")
        return value

    @field_validator("ollama_context_length")
    @classmethod
    def validate_ollama_context_length(cls, value: int) -> int:
        """Validate the positive Ollama model context length."""

        if value <= 0:
            raise ValueError("Ollama context length must be greater than zero.")
        return value

    @field_validator("ollama_keep_alive", mode="before")
    @classmethod
    def normalize_ollama_keep_alive(cls, value: str) -> str:
        """Trim and validate the Ollama model keep-alive value."""

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Ollama keep_alive must not be blank.")
        return normalized_value

    @field_validator("sidecar_auth_token", mode="before")
    @classmethod
    def normalize_sidecar_auth_token(cls, value: str | None) -> str | None:
        """Trim an optional sidecar token while rejecting blank configuration."""

        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Sidecar authentication token must be a string.")
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("Sidecar authentication token must not be blank.")
        return normalized_value

    @field_validator("sidecar_host", mode="before")
    @classmethod
    def normalize_sidecar_host(cls, value: str) -> str:
        """Allow only explicitly configured loopback host names or addresses."""

        if not isinstance(value, str):
            raise ValueError("Sidecar host must be a string.")
        normalized_value = value.strip().lower()
        if normalized_value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Sidecar host must be a loopback address.")
        return normalized_value

    @field_validator("sidecar_port")
    @classmethod
    def validate_sidecar_port(cls, value: int) -> int:
        """Validate the sidecar port, including the ephemeral-port sentinel."""

        if not 0 <= value <= 65_535:
            raise ValueError("Sidecar port must be between 0 and 65535.")
        return value

    @field_validator("sidecar_startup_timeout_seconds")
    @classmethod
    def validate_sidecar_startup_timeout_seconds(cls, value: float) -> float:
        """Validate the finite positive sidecar startup deadline."""

        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                "Sidecar startup timeout must be finite and greater than zero."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
