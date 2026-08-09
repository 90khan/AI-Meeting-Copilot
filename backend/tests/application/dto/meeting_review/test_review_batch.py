"""Tests for immutable Meeting review batch DTOs."""

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    BatchReviewResult,
    MeetingReviewContent,
    TranscriptReviewBatch,
)
from app.application.exceptions import ApplicationValidationError


def _content() -> MeetingReviewContent:
    return MeetingReviewContent(
        summary="Summary.",
        key_decisions=(),
        action_items=(),
        open_questions=(),
        technical_questions=(),
        technical_terms=(),
        feedback=None,
    )


def test_transcript_review_batch_preserves_immutable_tuple_data() -> None:
    batch = TranscriptReviewBatch(
        batch_index=0,
        start_transcript_index=2,
        end_transcript_index=3,
        transcript_ids=(UUID(int=1), UUID(int=2)),
        texts=("First.", "Second."),
        total_characters=len("First.") + len("Second."),
    )

    assert batch.transcript_ids == (UUID(int=1), UUID(int=2))
    with pytest.raises(FrozenInstanceError):
        batch.__setattr__("batch_index", 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"batch_index": -1},
        {"start_transcript_index": -1},
        {"end_transcript_index": -1},
        {"transcript_ids": [UUID(int=1)]},
        {"texts": ["Text."]},
        {"transcript_ids": ()},
        {"texts": ("",)},
        {"total_characters": 999},
    ],
)
def test_transcript_review_batch_rejects_invalid_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "batch_index": 0,
        "start_transcript_index": 0,
        "end_transcript_index": 0,
        "transcript_ids": (UUID(int=1),),
        "texts": ("Text.",),
        "total_characters": len("Text."),
    }
    values.update(overrides)

    with pytest.raises(ApplicationValidationError):
        TranscriptReviewBatch(**values)


def test_batch_review_result_requires_valid_index_and_content() -> None:
    result = BatchReviewResult(batch_index=0, content=_content())

    assert result.content.summary == "Summary."
    with pytest.raises(ApplicationValidationError):
        BatchReviewResult(batch_index=-1, content=_content())
    with pytest.raises(ApplicationValidationError):
        BatchReviewResult(batch_index=0, content="invalid")
