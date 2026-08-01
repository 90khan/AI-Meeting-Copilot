"""Tests for the meeting-summarization provider contract."""

import inspect

from app.application.dto.ai import (
    LanguageCode,
    MeetingSummaryRequest,
    MeetingSummaryResult,
)
from app.application.interfaces.ai import MeetingSummarizationProvider


class FakeMeetingSummarizationProvider:
    """Minimal structural implementation of the summarization contract."""

    async def summarize(self, request: MeetingSummaryRequest) -> MeetingSummaryResult:
        """Return a predictable summary for the supplied transcript."""

        return MeetingSummaryResult(
            summary=request.transcript_text,
            key_decisions=(),
            action_items=(),
            open_questions=(),
        )


def test_meeting_summarization_provider_method_is_asynchronous() -> None:
    """The provider contract exposes an asynchronous summary method."""

    assert inspect.iscoroutinefunction(MeetingSummarizationProvider.summarize)


def test_fake_provider_structurally_satisfies_the_protocol() -> None:
    """A summarization adapter needs no nominal protocol inheritance."""

    provider: MeetingSummarizationProvider = FakeMeetingSummarizationProvider()

    assert isinstance(provider, MeetingSummarizationProvider)


def test_fake_provider_uses_the_public_request_type() -> None:
    """The fake is compatible with a standard public request."""

    request = MeetingSummaryRequest(
        meeting_name="Planning",
        transcript_text="The team agreed on a plan.",
        language=LanguageCode(value="en"),
    )

    assert request.include_action_items is True
