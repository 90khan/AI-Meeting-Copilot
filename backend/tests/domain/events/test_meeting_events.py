"""Tests for concrete Meeting domain events."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from app.domain.entities import Meeting
from app.domain.events import (
    MeetingCreated,
    MeetingEnded,
    MeetingRenamed,
    MeetingStarted,
)
from app.domain.value_objects import MeetingId


def test_meeting_created_exposes_metadata_and_domain_payload() -> None:
    """A creation event carries the aggregate identity and its initial name."""

    meeting_id = MeetingId.new()

    event = MeetingCreated(aggregate_id=meeting_id, meeting_name="Product review")

    assert event.aggregate_id == meeting_id
    assert event.meeting_name == "Product review"
    assert event.event_type == "meeting.created"
    assert event.occurred_at.tzinfo is UTC


def test_meeting_started_exposes_its_event_type_and_timestamp() -> None:
    """A start event carries the lifecycle transition timestamp."""

    started_at = datetime(2026, 7, 31, tzinfo=UTC)

    event = MeetingStarted(aggregate_id=MeetingId.new(), started_at=started_at)

    assert event.started_at == started_at
    assert event.event_type == "meeting.started"


def test_meeting_ended_exposes_its_event_type_and_timestamp() -> None:
    """An end event carries the lifecycle transition timestamp."""

    ended_at = datetime(2026, 7, 31, tzinfo=UTC)

    event = MeetingEnded(aggregate_id=MeetingId.new(), ended_at=ended_at)

    assert event.ended_at == ended_at
    assert event.event_type == "meeting.ended"


def test_meeting_renamed_exposes_its_event_type_and_name_change() -> None:
    """A rename event carries both the old and new names."""

    event = MeetingRenamed(
        aggregate_id=MeetingId.new(),
        old_name="Product review",
        new_name="Customer interview",
    )

    assert event.old_name == "Product review"
    assert event.new_name == "Customer interview"
    assert event.event_type == "meeting.renamed"


def test_meeting_events_are_immutable() -> None:
    """Concrete Meeting events preserve the base event immutability guarantee."""

    event = MeetingCreated(aggregate_id=MeetingId.new(), meeting_name="Product review")

    with pytest.raises(FrozenInstanceError):
        event.meeting_name = "Customer interview"


def test_meeting_records_events_in_lifecycle_order_and_clears_them() -> None:
    """Aggregate changes record ordered events and pulling clears the queue."""

    meeting = Meeting.create(name="Product review")
    meeting.rename("Customer interview")
    meeting.start()
    meeting.end()

    events = meeting.pull_domain_events()

    assert tuple(type(event) for event in events) == (
        MeetingCreated,
        MeetingRenamed,
        MeetingStarted,
        MeetingEnded,
    )
    assert events[0].aggregate_id == meeting.id
    assert events[1].old_name == "Product review"
    assert events[1].new_name == "Customer interview"
    assert events[2].started_at == meeting.started_at
    assert events[3].ended_at == meeting.ended_at
    assert meeting.pull_domain_events() == ()
