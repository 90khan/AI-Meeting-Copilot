"""Tests for the immutable TranscriptEntry entity."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from app.domain.entities import TranscriptEntry
from app.domain.exceptions import ValidationError


def test_transcript_entries_with_the_same_identity_are_equal() -> None:
    """Transcript entries inherit identity-based entity equality."""

    entry_id = uuid4()
    timestamp = datetime(2026, 7, 31, tzinfo=UTC)

    assert TranscriptEntry(entry_id, "Alex", "Hello", timestamp) == TranscriptEntry(
        entry_id,
        "Jordan",
        "Different text",
        timestamp,
    )


def test_transcript_entry_is_immutable() -> None:
    """Transcript entry content cannot be reassigned after construction."""

    entry = TranscriptEntry(uuid4(), "Alex", "Hello", datetime(2026, 7, 31, tzinfo=UTC))

    with pytest.raises(AttributeError):
        entry.text = "Updated text"


@pytest.mark.parametrize("speaker", ["", "   "])
def test_transcript_entry_rejects_blank_speakers(speaker: str) -> None:
    """A speaker must contain non-whitespace characters."""

    with pytest.raises(ValidationError):
        TranscriptEntry(uuid4(), speaker, "Hello", datetime(2026, 7, 31, tzinfo=UTC))


@pytest.mark.parametrize("text", ["", "   "])
def test_transcript_entry_rejects_blank_text(text: str) -> None:
    """Transcript text must contain non-whitespace characters."""

    with pytest.raises(ValidationError):
        TranscriptEntry(uuid4(), "Alex", text, datetime(2026, 7, 31, tzinfo=UTC))


def test_transcript_entry_rejects_non_utc_or_naive_timestamps() -> None:
    """Transcript timestamps must be timezone-aware and use UTC."""

    entry_id = UUID("0ca8d51c-c7b0-406c-a4e4-2a40e54ba432")

    with pytest.raises(ValidationError):
        TranscriptEntry(entry_id, "Alex", "Hello", datetime(2026, 7, 31))
    with pytest.raises(ValidationError):
        TranscriptEntry(
            entry_id,
            "Alex",
            "Hello",
            datetime(2026, 7, 31, tzinfo=timezone(timedelta(hours=1))),
        )


def test_transcript_entry_exposes_its_utc_timestamp() -> None:
    """A valid transcript entry retains its UTC timestamp."""

    timestamp = datetime(2026, 7, 31, tzinfo=UTC)

    entry = TranscriptEntry(uuid4(), "Alex", "Hello", timestamp)

    assert entry.timestamp == timestamp
    assert entry.timestamp.tzinfo is UTC
