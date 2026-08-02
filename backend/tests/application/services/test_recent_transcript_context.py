"""Tests for session-local bounded finalized transcript context."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.dto.assist_mode import TranscriptSegment
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError
from app.application.services import RecentTranscriptContext
from app.domain.value_objects import MeetingId


def _segment(index: int, *, text: str | None = None) -> TranscriptSegment:
    return TranscriptSegment(
        transcript_id=uuid4(),
        meeting_id=MeetingId.new(),
        text=text if text is not None else f"segment-{index}",
        timestamp=datetime(2026, 8, 2, 10, 0, tzinfo=UTC) + timedelta(seconds=index),
        source=AudioSource.MIXED,
        speaker="Unknown",
    )


def test_empty_context_has_no_latest_or_reply_context() -> None:
    """An untouched session retains no finalized transcript context."""

    context = RecentTranscriptContext()

    assert context.is_empty() is True
    assert context.segment_count() == 0
    assert context.latest() is None
    assert context.build_reply_context() is None


def test_add_one_preserves_the_original_immutable_segment() -> None:
    """The service stores the exact immutable finalized segment instance."""

    context = RecentTranscriptContext()
    segment = _segment(1)
    context.add(segment)

    assert context.latest() is segment
    reply_context = context.build_reply_context()
    assert reply_context is not None
    assert reply_context.newest_finalized_segment is segment
    assert reply_context.previous_finalized_segments == ()
    with pytest.raises(FrozenInstanceError):
        segment.text = "changed"  # type: ignore[misc]


def test_context_keeps_newest_and_at_most_six_previous_in_order() -> None:
    """Adding many segments discards oldest entries while preserving chronology."""

    context = RecentTranscriptContext()
    segments = [_segment(index) for index in range(9)]
    for segment in segments:
        context.add(segment)

    reply_context = context.build_reply_context()
    assert reply_context is not None
    assert context.segment_count() == 7
    assert reply_context.previous_finalized_segments == tuple(segments[2:8])
    assert reply_context.newest_finalized_segment is segments[8]
    retained_segments = (
        *reply_context.previous_finalized_segments,
        reply_context.newest_finalized_segment,
    )
    assert [segment.text for segment in retained_segments] == [
        f"segment-{index}" for index in range(2, 9)
    ]


def test_character_limit_trims_oldest_segments_and_never_trims_newest() -> None:
    """The latest finalized text remains unchanged while old context is evicted."""

    context = RecentTranscriptContext()
    oldest = _segment(1, text="a" * 500)
    middle = _segment(2, text="b" * 500)
    newest = _segment(3, text="newest" * 100)
    for segment in (oldest, middle, newest):
        context.add(segment)

    reply_context = context.build_reply_context()
    assert reply_context is not None
    assert reply_context.newest_finalized_segment is newest
    assert newest.text == "newest" * 100
    assert oldest not in reply_context.previous_finalized_segments
    assert len(reply_context.conversation_text) <= 1_200


def test_clear_discards_only_the_session_memory() -> None:
    """Clear resets the in-memory session state without mutating segments."""

    context = RecentTranscriptContext()
    segment = _segment(1)
    context.add(segment)
    context.clear()

    assert context.is_empty() is True
    assert context.segment_count() == 0
    assert context.latest() is None
    assert segment.text == "segment-1"


def test_rejects_a_newest_segment_that_cannot_fit_without_trimming_it() -> None:
    """The newest segment is never shortened merely to satisfy reply-context bounds."""

    context = RecentTranscriptContext()

    with pytest.raises(ApplicationValidationError, match="Newest transcript segment"):
        context.add(_segment(1, text="x" * 1_201))
    assert context.is_empty() is True
