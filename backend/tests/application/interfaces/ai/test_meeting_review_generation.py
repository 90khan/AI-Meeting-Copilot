"""Tests for the structured Meeting review generation provider contract."""

import inspect
from uuid import UUID

from app.application.dto.meeting_review import (
    MeetingReviewContent,
    MeetingReviewGenerationRequest,
    MeetingReviewGenerationStage,
    TranscriptReviewBatch,
)
from app.application.interfaces.ai import MeetingReviewGenerationProvider
from app.domain.value_objects import MeetingId


class FakeMeetingReviewGenerationProvider:
    """Minimal structural implementation of the review-generation contract."""

    async def generate_review(
        self,
        request: MeetingReviewGenerationRequest,
    ) -> MeetingReviewContent:
        """Return a predictable structured result for the supplied request."""

        assert request.stage is MeetingReviewGenerationStage.BATCH
        return MeetingReviewContent(
            summary="Review summary.",
            key_decisions=(),
            action_items=(),
            open_questions=(),
            technical_questions=(),
            technical_terms=(),
            feedback=None,
        )


def test_review_generation_provider_method_is_asynchronous() -> None:
    """The provider contract exposes one asynchronous structured method."""

    assert inspect.iscoroutinefunction(MeetingReviewGenerationProvider.generate_review)


def test_fake_provider_structurally_satisfies_the_protocol() -> None:
    """Adapters need no nominal inheritance from the application interface."""

    provider: MeetingReviewGenerationProvider = FakeMeetingReviewGenerationProvider()

    assert isinstance(provider, MeetingReviewGenerationProvider)


def test_provider_uses_the_public_structured_request_type() -> None:
    """The contract consumes a batch-only review generation request."""

    request = MeetingReviewGenerationRequest(
        meeting_id=MeetingId(UUID(int=1)),
        stage=MeetingReviewGenerationStage.BATCH,
        batch=TranscriptReviewBatch(
            batch_index=0,
            start_transcript_index=0,
            end_transcript_index=0,
            transcript_ids=(UUID(int=2),),
            texts=("One transcript entry.",),
            total_characters=len("One transcript entry."),
        ),
        intermediate_content=None,
    )

    assert request.stage is MeetingReviewGenerationStage.BATCH
