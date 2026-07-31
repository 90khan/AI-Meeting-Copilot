"""Tests for Meeting lifecycle statuses."""

from app.domain.value_objects import MeetingStatus


def test_meeting_status_members_have_expected_values() -> None:
    """Each lifecycle state has its stable string value."""

    assert tuple(MeetingStatus) == (
        MeetingStatus.DRAFT,
        MeetingStatus.ACTIVE,
        MeetingStatus.ENDED,
    )


def test_meeting_statuses_are_strings() -> None:
    """StrEnum members retain their string representation."""

    assert MeetingStatus.DRAFT == "draft"
    assert str(MeetingStatus.ACTIVE) == "active"
    assert MeetingStatus.ENDED.value == "ended"


def test_meeting_status_equality_uses_member_values() -> None:
    """Equivalent status members compare equal."""

    assert MeetingStatus("draft") == MeetingStatus.DRAFT
