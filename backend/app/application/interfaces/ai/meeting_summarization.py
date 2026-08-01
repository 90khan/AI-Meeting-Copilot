"""Meeting-summarization provider contract."""

from typing import Protocol, runtime_checkable

from app.application.dto.ai.summarization import (
    MeetingSummaryRequest,
    MeetingSummaryResult,
)


@runtime_checkable
class MeetingSummarizationProvider(Protocol):
    """Provides asynchronous meeting summarization."""

    async def summarize(self, request: MeetingSummaryRequest) -> MeetingSummaryResult:
        """Summarize a complete meeting transcript."""
