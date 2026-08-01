"""Tests for the reply-coaching provider contract."""

import inspect

from app.application.dto.ai import (
    LanguageCode,
    ReplyCoachingRequest,
    ReplyCoachingResult,
    ReplySuggestion,
    ReplyTone,
)
from app.application.interfaces.ai import ReplyCoachingProvider


class FakeReplyCoachingProvider:
    """Minimal structural implementation of the reply-coaching contract."""

    async def suggest_replies(
        self, request: ReplyCoachingRequest
    ) -> ReplyCoachingResult:
        """Return one predictable reply suggestion."""

        return ReplyCoachingResult(
            suggestions=(
                ReplySuggestion(text=request.latest_utterance, tone=request.tone),
            )
        )


def test_reply_coaching_provider_method_is_asynchronous() -> None:
    """The provider contract exposes an asynchronous suggestion method."""

    assert inspect.iscoroutinefunction(ReplyCoachingProvider.suggest_replies)


def test_fake_provider_structurally_satisfies_the_protocol() -> None:
    """A reply-coaching adapter needs no nominal protocol inheritance."""

    provider: ReplyCoachingProvider = FakeReplyCoachingProvider()

    assert isinstance(provider, ReplyCoachingProvider)


def test_fake_provider_uses_the_public_request_type() -> None:
    """The fake is compatible with a standard public request."""

    request = ReplyCoachingRequest(
        conversation_context="Planning discussion",
        latest_utterance="Can you confirm?",
        response_language=LanguageCode(value="en"),
    )

    assert request.tone is ReplyTone.PROFESSIONAL
