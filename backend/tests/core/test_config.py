"""Tests for AI provider-selection settings."""

from pathlib import Path

import pytest
from app.core.config import Settings
from pydantic import ValidationError


def test_provider_settings_use_safe_defaults() -> None:
    """Every AI capability defaults to an explicit unconfigured provider."""

    settings = Settings()

    assert settings.speech_to_text_provider == "unconfigured"
    assert settings.translation_provider == "unconfigured"
    assert settings.german_simplification_provider == "unconfigured"
    assert settings.reply_coaching_provider == "unconfigured"
    assert settings.meeting_summarization_provider == "unconfigured"


def test_provider_settings_read_prefixed_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider names use the existing application environment prefix."""

    monkeypatch.setenv("AI_MEETING_COPILOT_TRANSLATION_PROVIDER", " OpenAI ")

    settings = Settings()

    assert settings.translation_provider == "openai"


@pytest.mark.parametrize(
    "field_name",
    [
        "speech_to_text_provider",
        "translation_provider",
        "german_simplification_provider",
        "reply_coaching_provider",
        "meeting_summarization_provider",
    ],
)
def test_provider_settings_normalize_values(field_name: str) -> None:
    """Provider names are trimmed and normalized to lowercase."""

    settings = Settings(**{field_name: "  Local-Provider  "})

    assert getattr(settings, field_name) == "local-provider"


@pytest.mark.parametrize(
    "field_name",
    [
        "speech_to_text_provider",
        "translation_provider",
        "german_simplification_provider",
        "reply_coaching_provider",
        "meeting_summarization_provider",
    ],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_provider_settings_reject_blank_values(field_name: str, value: str) -> None:
    """Provider settings must contain a non-blank name."""

    with pytest.raises(ValidationError, match="Provider name must not be blank"):
        Settings(**{field_name: value})


def test_faster_whisper_settings_use_local_cpu_defaults() -> None:
    """Faster-Whisper settings default to a local CPU-friendly configuration."""

    settings = Settings()

    assert settings.faster_whisper_model == "small"
    assert settings.faster_whisper_device == "cpu"
    assert settings.faster_whisper_compute_type == "int8"
    assert settings.faster_whisper_cpu_threads is None
    assert settings.faster_whisper_beam_size == 5
    assert settings.faster_whisper_vad_enabled is False
    assert settings.faster_whisper_download_directory is None


def test_faster_whisper_settings_read_prefixed_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Faster-Whisper settings use the existing application environment prefix."""

    monkeypatch.setenv("AI_MEETING_COPILOT_FASTER_WHISPER_MODEL", "  Custom-Model  ")
    monkeypatch.setenv("AI_MEETING_COPILOT_FASTER_WHISPER_DEVICE", " CPU ")
    monkeypatch.setenv("AI_MEETING_COPILOT_FASTER_WHISPER_BEAM_SIZE", "7")
    monkeypatch.setenv("AI_MEETING_COPILOT_FASTER_WHISPER_VAD_ENABLED", "true")

    settings = Settings()

    assert settings.faster_whisper_model == "Custom-Model"
    assert settings.faster_whisper_device == "cpu"
    assert settings.faster_whisper_beam_size == 7
    assert settings.faster_whisper_vad_enabled is True


def test_faster_whisper_runtime_strings_are_normalized() -> None:
    """Device and compute type are trimmed and normalized to lowercase."""

    settings = Settings(
        faster_whisper_model="  Custom-Model  ",
        faster_whisper_device=" CPU ",
        faster_whisper_compute_type=" INT8_FLOAT32 ",
    )

    assert settings.faster_whisper_model == "Custom-Model"
    assert settings.faster_whisper_device == "cpu"
    assert settings.faster_whisper_compute_type == "int8_float32"


@pytest.mark.parametrize(
    "field_name",
    [
        "faster_whisper_model",
        "faster_whisper_device",
        "faster_whisper_compute_type",
    ],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_faster_whisper_settings_reject_blank_runtime_strings(
    field_name: str, value: str
) -> None:
    """Required Faster-Whisper string settings must not be blank."""

    with pytest.raises(ValidationError, match="Faster-Whisper"):
        Settings(**{field_name: value})


@pytest.mark.parametrize("cpu_threads", [0, -1])
def test_faster_whisper_settings_reject_invalid_cpu_threads(cpu_threads: int) -> None:
    """An explicit CPU thread count must be positive."""

    with pytest.raises(ValidationError, match="CPU threads"):
        Settings(faster_whisper_cpu_threads=cpu_threads)


@pytest.mark.parametrize("beam_size", [0, 11])
def test_faster_whisper_settings_reject_invalid_beam_size(beam_size: int) -> None:
    """Beam-search width must remain within the V1 bounds."""

    with pytest.raises(ValidationError, match="beam size"):
        Settings(faster_whisper_beam_size=beam_size)


def test_faster_whisper_download_directory_is_expanded_and_resolved() -> None:
    """A configured model directory is absolute without being created."""

    settings = Settings(faster_whisper_download_directory="~/models")

    assert (
        settings.faster_whisper_download_directory == (Path.home() / "models").resolve()
    )
    assert settings.faster_whisper_download_directory.is_absolute()


def test_settings_remain_immutable() -> None:
    """Faster-Whisper configuration retains the immutable settings policy."""

    settings = Settings()

    with pytest.raises(ValidationError):
        settings.faster_whisper_model = "base"  # type: ignore[misc]
