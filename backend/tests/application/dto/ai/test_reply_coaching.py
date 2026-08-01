"""Tests for reply-coaching application DTOs."""

from dataclasses import FrozenInstanceError

import pytest
from app.application.dto.ai import (
    GermanLevel,
    LanguageCode,
    ReplyCoachingRequest,
    ReplyCoachingResult,
    ReplySuggestion,
    ReplyTone,
)
from app.application.exceptions import ApplicationValidationError


def make_request(**overrides: object) -> ReplyCoachingRequest:
    """Create a valid reply-coaching request for tests."""

    values: dict[str, object] = {
        "conversation_context": "We are discussing the delivery timeline.",
        "latest_utterance": "Can you share the revised date?",
        "response_language": LanguageCode(value="en"),
    }
    values.update(overrides)
    return ReplyCoachingRequest(**values)  # type: ignore[arg-type]


def test_reply_tones_have_the_expected_string_values() -> None:
    """Reply tones use the supported string values."""

    assert [tone.value for tone in ReplyTone] == [
        "neutral",
        "professional",
        "friendly",
        "confident",
    ]
    assert ReplyTone.PROFESSIONAL == "professional"


def test_reply_coaching_request_is_immutable() -> None:
    """Reply-coaching request fields cannot change after construction."""

    request = make_request()

    with pytest.raises(FrozenInstanceError):
        request.max_suggestions = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    ("conversation_context", "latest_utterance"),
    [("", "Hello"), ("Context", "   ")],
)
def test_reply_coaching_request_rejects_blank_input(
    conversation_context: str, latest_utterance: str
) -> None:
    """Conversation context and latest utterance are both required."""

    with pytest.raises(ApplicationValidationError):
        make_request(
            conversation_context=conversation_context,
            latest_utterance=latest_utterance,
        )


@pytest.mark.parametrize("max_suggestions", [0, 4])
def test_reply_coaching_request_validates_max_suggestions(
    max_suggestions: int,
) -> None:
    """Only one to three suggestions may be requested."""

    with pytest.raises(ApplicationValidationError):
        make_request(max_suggestions=max_suggestions)


def test_target_german_level_requires_a_german_response_language() -> None:
    """German complexity guidance is valid only for German responses."""

    with pytest.raises(ApplicationValidationError):
        make_request(target_german_level=GermanLevel.B1)


def test_target_german_level_accepts_a_german_regional_language_code() -> None:
    """Canonical German regional codes support German-level guidance."""

    request = make_request(
        response_language=LanguageCode(value="de-DE"),
        target_german_level=GermanLevel.B2,
    )

    assert request.target_german_level is GermanLevel.B2


def test_reply_suggestion_rejects_blank_text() -> None:
    """A reply suggestion requires text."""

    with pytest.raises(ApplicationValidationError):
        ReplySuggestion(text=" ", tone=ReplyTone.NEUTRAL)


def test_reply_coaching_result_is_immutable() -> None:
    """Reply-coaching result fields cannot change after construction."""

    result = ReplyCoachingResult(
        suggestions=(
            ReplySuggestion(text="I will check.", tone=ReplyTone.PROFESSIONAL),
        )
    )

    with pytest.raises(FrozenInstanceError):
        result.suggestions = ()  # type: ignore[misc]


def test_reply_coaching_result_requires_one_to_three_suggestions() -> None:
    """Results require a non-empty bounded suggestion collection."""

    suggestion = ReplySuggestion(text="I will check.", tone=ReplyTone.PROFESSIONAL)

    with pytest.raises(ApplicationValidationError):
        ReplyCoachingResult(suggestions=())
    with pytest.raises(ApplicationValidationError):
        ReplyCoachingResult(
            suggestions=(suggestion, suggestion, suggestion, suggestion)
        )
