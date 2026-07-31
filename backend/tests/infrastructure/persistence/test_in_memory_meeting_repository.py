"""Tests for the in-memory Meeting repository adapter."""

import asyncio

from app.domain.entities import Meeting
from app.domain.events import MeetingCreated
from app.domain.value_objects import MeetingId
from app.infrastructure.persistence import InMemoryMeetingRepository


def test_save_then_load_preserves_the_meeting_identity() -> None:
    """Saving and loading returns the aggregate stored under its identity."""

    repository = InMemoryMeetingRepository()
    meeting = Meeting.create(name="Product review")

    asyncio.run(repository.save(meeting))
    loaded_meeting = asyncio.run(repository.get_by_id(meeting.id))

    assert loaded_meeting is meeting
    assert loaded_meeting.id == meeting.id


def test_save_overwrites_an_existing_meeting() -> None:
    """Saving another aggregate with the same identity replaces the prior one."""

    repository = InMemoryMeetingRepository()
    meeting_id = MeetingId.new()
    original_meeting = Meeting.create(meeting_id=meeting_id, name="Product review")
    replacement_meeting = Meeting.create(
        meeting_id=meeting_id,
        name="Customer interview",
    )

    asyncio.run(repository.save(original_meeting))
    asyncio.run(repository.save(replacement_meeting))

    assert asyncio.run(repository.get_by_id(meeting_id)) is replacement_meeting


def test_delete_removes_a_stored_meeting() -> None:
    """Deleting a stored Meeting makes subsequent lookup return None."""

    repository = InMemoryMeetingRepository()
    meeting = Meeting.create(name="Product review")
    asyncio.run(repository.save(meeting))

    asyncio.run(repository.delete(meeting))

    assert asyncio.run(repository.get_by_id(meeting.id)) is None


def test_delete_missing_meeting_does_not_raise() -> None:
    """Deleting an absent Meeting is a no-op."""

    repository = InMemoryMeetingRepository()

    asyncio.run(repository.delete(Meeting.create(name="Product review")))


def test_get_by_id_returns_none_when_the_meeting_is_missing() -> None:
    """Lookup returns None for an identity not present in storage."""

    repository = InMemoryMeetingRepository()

    assert asyncio.run(repository.get_by_id(MeetingId.new())) is None


def test_save_preserves_pending_domain_events() -> None:
    """Persisting does not consume or otherwise alter aggregate domain events."""

    repository = InMemoryMeetingRepository()
    meeting = Meeting.create(name="Product review")

    asyncio.run(repository.save(meeting))
    loaded_meeting = asyncio.run(repository.get_by_id(meeting.id))

    assert loaded_meeting is not None
    (event,) = loaded_meeting.pull_domain_events()
    assert isinstance(event, MeetingCreated)
