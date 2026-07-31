"""Tests for the Meeting aggregate's core lifecycle."""

from datetime import UTC

import pytest
from app.domain.entities import Meeting
from app.domain.events import MeetingCreated
from app.domain.exceptions import InvalidStateTransitionError, ValidationError
from app.domain.value_objects import MeetingId, MeetingStatus


def test_create_returns_a_draft_meeting_with_default_state() -> None:
    """A newly created Meeting is a draft without lifecycle timestamps."""

    meeting = Meeting.create(name="Product review")

    assert isinstance(meeting.id, MeetingId)
    assert meeting.name == "Product review"
    assert meeting.status is MeetingStatus.DRAFT
    assert meeting.started_at is None
    assert meeting.ended_at is None

    (event,) = meeting.pull_domain_events()

    assert isinstance(event, MeetingCreated)
    assert event.aggregate_id == meeting.id
    assert event.meeting_name == "Product review"


def test_create_preserves_an_explicit_meeting_id() -> None:
    """An explicit identity is retained when creating a Meeting."""

    meeting_id = MeetingId.new()

    meeting = Meeting.create(meeting_id=meeting_id, name="Product review")

    assert meeting.id == meeting_id


def test_start_activates_a_draft_meeting() -> None:
    """Starting a draft Meeting marks it active and records its start time."""

    meeting = Meeting.create(name="Product review")

    meeting.start()

    assert meeting.status is MeetingStatus.ACTIVE
    assert meeting.started_at is not None
    assert meeting.started_at.tzinfo is UTC
    assert meeting.ended_at is None


def test_end_completes_an_active_meeting() -> None:
    """Ending an active Meeting records an ordered lifecycle timestamp."""

    meeting = Meeting.create(name="Product review")
    meeting.start()

    meeting.end()

    assert meeting.status is MeetingStatus.ENDED
    assert meeting.started_at is not None
    assert meeting.ended_at is not None
    assert meeting.ended_at >= meeting.started_at
    assert meeting.ended_at.tzinfo is UTC


def test_rename_updates_a_meeting_that_has_not_ended() -> None:
    """Draft and active Meetings can be renamed."""

    meeting = Meeting.create(name="Product review")

    meeting.rename("Customer interview")

    assert meeting.name == "Customer interview"


@pytest.mark.parametrize("name", ["", "   "])
def test_create_rejects_blank_names(name: str) -> None:
    """A Meeting name must contain non-whitespace characters."""

    with pytest.raises(ValidationError):
        Meeting.create(name=name)


@pytest.mark.parametrize("name", ["", "   "])
def test_rename_rejects_blank_names(name: str) -> None:
    """Renaming uses the same name validation as creation."""

    meeting = Meeting.create(name="Product review")

    with pytest.raises(ValidationError):
        meeting.rename(name)


def test_start_rejects_a_meeting_that_is_not_a_draft() -> None:
    """An active Meeting cannot be started again."""

    meeting = Meeting.create(name="Product review")
    meeting.start()

    with pytest.raises(InvalidStateTransitionError):
        meeting.start()


def test_end_rejects_a_meeting_that_is_not_active() -> None:
    """A draft Meeting cannot be ended."""

    meeting = Meeting.create(name="Product review")

    with pytest.raises(InvalidStateTransitionError):
        meeting.end()


def test_ended_meeting_cannot_be_renamed_or_ended_again() -> None:
    """Ending a Meeting closes its mutable lifecycle."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.end()

    with pytest.raises(InvalidStateTransitionError):
        meeting.rename("Customer interview")
    with pytest.raises(InvalidStateTransitionError):
        meeting.end()
