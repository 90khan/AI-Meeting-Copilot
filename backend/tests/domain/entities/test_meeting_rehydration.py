"""Tests for Meeting aggregate rehydration."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.domain.entities import Meeting, TranscriptEntry
from app.domain.exceptions import InvariantViolationError, ValidationError
from app.domain.value_objects import MeetingId, MeetingStatus


def test_rehydrate_restores_a_draft_meeting_without_events() -> None:
    """Draft state is restored without creating new domain events."""

    meeting_id = MeetingId.new()

    meeting = Meeting.rehydrate(
        meeting_id=meeting_id,
        name="Product review",
        status=MeetingStatus.DRAFT,
        started_at=None,
        ended_at=None,
    )

    assert meeting.id == meeting_id
    assert meeting.name == "Product review"
    assert meeting.status is MeetingStatus.DRAFT
    assert meeting.started_at is None
    assert meeting.ended_at is None
    assert meeting.pull_domain_events() == ()


def test_rehydrate_restores_an_active_meeting() -> None:
    """Active state retains its UTC start timestamp."""

    started_at = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

    meeting = Meeting.rehydrate(
        meeting_id=MeetingId.new(),
        name="Product review",
        status=MeetingStatus.ACTIVE,
        started_at=started_at,
        ended_at=None,
    )

    assert meeting.status is MeetingStatus.ACTIVE
    assert meeting.started_at == started_at
    assert meeting.ended_at is None


def test_rehydrate_restores_an_ended_meeting() -> None:
    """Ended state retains ordered UTC lifecycle timestamps."""

    started_at = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    ended_at = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

    meeting = Meeting.rehydrate(
        meeting_id=MeetingId.new(),
        name="Product review",
        status=MeetingStatus.ENDED,
        started_at=started_at,
        ended_at=ended_at,
    )

    assert meeting.status is MeetingStatus.ENDED
    assert meeting.started_at == started_at
    assert meeting.ended_at == ended_at


def test_rehydrate_restores_transcripts_in_order_as_a_tuple() -> None:
    """Persisted transcript entries retain ordering and read-only exposure."""

    first_entry = TranscriptEntry(
        MeetingId.new().value,
        "Alex",
        "Welcome everyone.",
        datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )
    second_entry = TranscriptEntry(
        MeetingId.new().value,
        "Jordan",
        "Thank you.",
        datetime(2026, 8, 1, 9, 1, tzinfo=UTC),
    )

    meeting = Meeting.rehydrate(
        meeting_id=MeetingId.new(),
        name="Product review",
        status=MeetingStatus.DRAFT,
        started_at=None,
        ended_at=None,
        transcripts=(first_entry, second_entry),
    )

    assert meeting.transcripts == (first_entry, second_entry)
    assert isinstance(meeting.transcripts, tuple)


@pytest.mark.parametrize(
    ("status", "started_at", "ended_at"),
    [
        (MeetingStatus.DRAFT, datetime(2026, 8, 1, tzinfo=UTC), None),
        (MeetingStatus.DRAFT, None, datetime(2026, 8, 1, tzinfo=UTC)),
        (MeetingStatus.ACTIVE, None, None),
        (
            MeetingStatus.ACTIVE,
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        ),
        (MeetingStatus.ENDED, None, datetime(2026, 8, 1, tzinfo=UTC)),
        (MeetingStatus.ENDED, datetime(2026, 8, 1, tzinfo=UTC), None),
        (
            MeetingStatus.ENDED,
            datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        ),
    ],
)
def test_rehydrate_rejects_impossible_lifecycle_combinations(
    status: MeetingStatus,
    started_at: datetime | None,
    ended_at: datetime | None,
) -> None:
    """Persisted lifecycle combinations must remain internally consistent."""

    with pytest.raises(InvariantViolationError):
        Meeting.rehydrate(
            meeting_id=MeetingId.new(),
            name="Product review",
            status=status,
            started_at=started_at,
            ended_at=ended_at,
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 1),
        datetime(2026, 8, 1, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_rehydrate_rejects_non_utc_timestamps(timestamp: datetime) -> None:
    """Persisted timestamps must be timezone-aware UTC values."""

    with pytest.raises(ValidationError):
        Meeting.rehydrate(
            meeting_id=MeetingId.new(),
            name="Product review",
            status=MeetingStatus.ACTIVE,
            started_at=timestamp,
            ended_at=None,
        )
