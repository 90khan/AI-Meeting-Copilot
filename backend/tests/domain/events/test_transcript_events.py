"""Tests for transcript domain events."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from app.domain.events import TranscriptAdded
from app.domain.value_objects import MeetingId


def test_transcript_added_exposes_its_payload_and_event_type() -> None:
    """The event identifies the aggregate, transcript, and speaker."""

    meeting_id = MeetingId.new()
    transcript_id = uuid4()

    event = TranscriptAdded(
        aggregate_id=meeting_id,
        transcript_id=transcript_id,
        speaker="Alex",
    )

    assert event.aggregate_id == meeting_id
    assert event.transcript_id == transcript_id
    assert event.speaker == "Alex"
    assert event.event_type == "transcript.added"


def test_transcript_added_is_immutable() -> None:
    """Transcript events cannot be changed after construction."""

    event = TranscriptAdded(
        aggregate_id=MeetingId.new(),
        transcript_id=uuid4(),
        speaker="Alex",
    )

    with pytest.raises(FrozenInstanceError):
        event.speaker = "Jordan"
