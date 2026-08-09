"""Structured Meeting review generation provider contract."""

from typing import Protocol, runtime_checkable

from app.application.dto.meeting_review import (
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
)


@runtime_checkable
class MeetingReviewGenerationProvider(Protocol):
    """Generate typed review content for a batch or deterministic reduce input."""

    async def generate_review(
        self,
        request: MeetingReviewGenerationRequest,
    ) -> MeetingReviewContent:
        """Return structured review content without transport concerns."""

        ...
