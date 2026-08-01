"""Tests for Meeting domain-to-ORM mapping."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.domain.entities import Meeting
from app.domain.events import MeetingCreated, MeetingEnded, MeetingStarted
from app.domain.exceptions import ValidationError
from app.domain.value_objects import MeetingStatus
from app.infrastructure.persistence.sqlalchemy.mappers import MeetingMapper
from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    TranscriptEntryModel,
)


def test_draft_meeting_round_trip_preserves_state_and_pending_events() -> None:
    """A draft Meeting maps to a detached model and rehydrates without events."""

    meeting = Meeting.create(name="Product review")

    model = MeetingMapper.to_model(meeting)
    restored_meeting = MeetingMapper.to_domain(model)

    assert model.id == str(meeting.id)
    assert model.status == MeetingStatus.DRAFT.value
    assert restored_meeting.id == meeting.id
    assert restored_meeting.name == meeting.name
    assert restored_meeting.status is MeetingStatus.DRAFT
    assert restored_meeting.pull_domain_events() == ()

    (event,) = meeting.pull_domain_events()
    assert isinstance(event, MeetingCreated)


def test_active_meeting_round_trip_preserves_state() -> None:
    """An active Meeting retains its lifecycle state and start timestamp."""

    meeting = Meeting.create(name="Product review")
    meeting.start()

    restored_meeting = MeetingMapper.to_domain(MeetingMapper.to_model(meeting))

    assert restored_meeting.status is MeetingStatus.ACTIVE
    assert restored_meeting.started_at == meeting.started_at
    assert restored_meeting.ended_at is None


def test_ended_meeting_round_trip_preserves_state() -> None:
    """An ended Meeting retains both lifecycle timestamps."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.end()

    restored_meeting = MeetingMapper.to_domain(MeetingMapper.to_model(meeting))

    assert restored_meeting.status is MeetingStatus.ENDED
    assert restored_meeting.started_at == meeting.started_at
    assert restored_meeting.ended_at == meeting.ended_at


def test_transcript_mapping_preserves_ids_order_and_generated_sequences() -> None:
    """Transcript model rows follow aggregate order and preserve entry identities."""

    meeting = Meeting.create(name="Product review")
    first_entry = meeting.add_transcript(
        speaker="Alex",
        text="Welcome everyone.",
        timestamp=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )
    second_entry = meeting.add_transcript(
        speaker="Jordan",
        text="Thank you.",
        timestamp=datetime(2026, 8, 1, 9, 1, tzinfo=UTC),
    )

    model = MeetingMapper.to_model(meeting)
    restored_meeting = MeetingMapper.to_domain(model)

    assert [transcript.sequence for transcript in model.transcripts] == [0, 1]
    assert [transcript.id for transcript in model.transcripts] == [
        str(first_entry.id),
        str(second_entry.id),
    ]
    assert restored_meeting.transcripts == (first_entry, second_entry)


def test_mapper_does_not_create_or_remove_domain_events() -> None:
    """Mapping preserves pending source events and emits none on rehydration."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.end()

    restored_meeting = MeetingMapper.to_domain(MeetingMapper.to_model(meeting))

    assert tuple(type(event) for event in meeting.pull_domain_events()) == (
        MeetingCreated,
        MeetingStarted,
        MeetingEnded,
    )
    assert restored_meeting.pull_domain_events() == ()


def test_naive_persisted_datetimes_are_normalized_to_utc() -> None:
    """SQLite-style naïve timestamps become UTC-aware before rehydration."""

    model = MeetingModel(
        id="04a5de3d-dbf9-4f3f-99f9-69a2e18ce45e",
        name="Product review",
        status=MeetingStatus.ACTIVE.value,
        started_at=datetime(2026, 8, 1, 9, 0),
        ended_at=None,
        transcripts=[
            TranscriptEntryModel(
                id="06fd84cd-0471-43f8-8d76-27a4d479ced3",
                meeting_id="04a5de3d-dbf9-4f3f-99f9-69a2e18ce45e",
                speaker="Alex",
                text="Welcome everyone.",
                timestamp=datetime(2026, 8, 1, 9, 0),
                sequence=0,
            )
        ],
    )

    meeting = MeetingMapper.to_domain(model)

    assert meeting.started_at is not None
    assert meeting.started_at.tzinfo is UTC
    assert meeting.transcripts[0].timestamp.tzinfo is UTC


def test_non_utc_aware_persisted_datetime_is_rejected() -> None:
    """Aware persisted timestamps with non-UTC offsets are invalid."""

    model = MeetingModel(
        id="04a5de3d-dbf9-4f3f-99f9-69a2e18ce45e",
        name="Product review",
        status=MeetingStatus.ACTIVE.value,
        started_at=datetime(
            2026,
            8,
            1,
            9,
            0,
            tzinfo=timezone(timedelta(hours=1)),
        ),
        ended_at=None,
    )

    with pytest.raises(ValidationError, match="Persisted timestamps must use UTC"):
        MeetingMapper.to_domain(model)


def test_invalid_persisted_status_is_rejected() -> None:
    """Unknown persisted lifecycle values are rejected before rehydration."""

    model = MeetingModel(
        id="04a5de3d-dbf9-4f3f-99f9-69a2e18ce45e",
        name="Product review",
        status="unknown",
        started_at=None,
        ended_at=None,
    )

    with pytest.raises(ValidationError, match="Meeting status is not supported"):
        MeetingMapper.to_domain(model)
