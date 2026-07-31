"""Tests for the Meeting identity value object."""

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from app.domain.value_objects import MeetingId


def test_meeting_ids_with_the_same_uuid_are_equal() -> None:
    """Meeting identities compare by their wrapped UUID value."""

    meeting_uuid = UUID("b8e2a98c-9230-4e86-a118-5e1de3a7f026")

    assert MeetingId(meeting_uuid) == MeetingId(meeting_uuid)


def test_meeting_id_is_immutable() -> None:
    """A Meeting identity cannot be changed after construction."""

    meeting_id = MeetingId.new()

    with pytest.raises(FrozenInstanceError):
        meeting_id.value = UUID("b8e2a98c-9230-4e86-a118-5e1de3a7f026")


def test_new_meeting_id_generates_a_uuid() -> None:
    """The identity factory creates a UUID-backed identity."""

    meeting_id = MeetingId.new()

    assert isinstance(meeting_id.value, UUID)


def test_meeting_id_preserves_an_explicit_uuid() -> None:
    """An explicit UUID supports persistence rehydration."""

    meeting_uuid = UUID("b8e2a98c-9230-4e86-a118-5e1de3a7f026")

    assert MeetingId(meeting_uuid).value == meeting_uuid
    assert str(MeetingId(meeting_uuid)) == str(meeting_uuid)
