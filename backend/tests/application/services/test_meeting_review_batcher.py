"""Tests for deterministic whole-segment Meeting review batching."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto import AudioSource
from app.application.dto.meeting_review import TranscriptReadItem
from app.application.exceptions import ApplicationValidationError
from app.application.services import MeetingReviewBatcher


def _entry(index: int, text: str) -> TranscriptReadItem:
    return TranscriptReadItem(
        transcript_id=UUID(int=index + 1),
        text=text,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        speaker="Unknown",
        source=AudioSource.MIXED,
    )


def test_empty_transcript_returns_no_batches() -> None:
    assert MeetingReviewBatcher(max_batch_characters=10).build_batches(()) == ()


def test_single_entry_and_exact_boundary_fit_are_preserved() -> None:
    entry = _entry(0, "12345")

    batches = MeetingReviewBatcher(max_batch_characters=5).build_batches((entry,))

    assert len(batches) == 1
    assert batches[0].start_transcript_index == 0
    assert batches[0].end_transcript_index == 0
    assert batches[0].texts == ("12345",)
    assert batches[0].total_characters == 5


def test_batches_preserve_order_and_whole_segment_overlap() -> None:
    transcript = tuple(_entry(index, "12345") for index in range(4))

    batches = MeetingReviewBatcher(
        max_batch_characters=10,
        overlap_segments=1,
    ).build_batches(transcript)

    assert [batch.transcript_ids for batch in batches] == [
        (UUID(int=1), UUID(int=2)),
        (UUID(int=2), UUID(int=3)),
        (UUID(int=3), UUID(int=4)),
    ]
    assert [batch.batch_index for batch in batches] == [0, 1, 2]
    assert all(batch.total_characters == 10 for batch in batches)


def test_zero_and_multi_segment_overlap_follow_batch_boundaries() -> None:
    transcript = tuple(_entry(index, "12345") for index in range(5))

    no_overlap = MeetingReviewBatcher(
        max_batch_characters=10,
        overlap_segments=0,
    ).build_batches(transcript)
    multi_overlap = MeetingReviewBatcher(
        max_batch_characters=15,
        overlap_segments=2,
    ).build_batches(transcript)

    assert [batch.transcript_ids for batch in no_overlap] == [
        (UUID(int=1), UUID(int=2)),
        (UUID(int=3), UUID(int=4)),
        (UUID(int=5),),
    ]
    assert [batch.transcript_ids for batch in multi_overlap] == [
        (UUID(int=1), UUID(int=2), UUID(int=3)),
        (UUID(int=2), UUID(int=3), UUID(int=4)),
        (UUID(int=3), UUID(int=4), UUID(int=5)),
    ]


def test_single_oversized_entry_is_emitted_alone() -> None:
    batches = MeetingReviewBatcher(max_batch_characters=5).build_batches(
        (_entry(0, "oversized"), _entry(1, "small"))
    )

    assert [batch.texts for batch in batches] == [("oversized",), ("small",)]
    assert batches[0].total_characters > 5


def test_long_transcript_loses_no_entry_and_is_deterministic() -> None:
    transcript = tuple(_entry(index, f"Entry {index}.") for index in range(2_001))
    batcher = MeetingReviewBatcher(max_batch_characters=100, overlap_segments=1)

    first = batcher.build_batches(transcript)
    second = batcher.build_batches(transcript)
    first_seen_ids = {
        transcript_id for batch in first for transcript_id in batch.transcript_ids
    }

    assert first == second
    assert first_seen_ids == {entry.transcript_id for entry in transcript}
    assert all(batch.batch_index == index for index, batch in enumerate(first))
    assert all(
        batch.total_characters <= 100 or len(batch.transcript_ids) == 1
        for batch in first
    )


@pytest.mark.parametrize(
    ("max_batch_characters", "overlap_segments"),
    [(0, 0), (-1, 0), (1, -1)],
)
def test_invalid_batch_configuration_is_rejected(
    max_batch_characters: int,
    overlap_segments: int,
) -> None:
    with pytest.raises(ApplicationValidationError):
        MeetingReviewBatcher(
            max_batch_characters=max_batch_characters,
            overlap_segments=overlap_segments,
        )
