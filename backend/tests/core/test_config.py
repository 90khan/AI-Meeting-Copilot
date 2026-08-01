"""Tests for AI provider-selection settings."""

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
