"""Lifecycle states for Meeting aggregates."""

from enum import StrEnum


class MeetingStatus(StrEnum):
    """The allowed lifecycle states of a Meeting."""

    DRAFT = "draft"
    ACTIVE = "active"
    ENDED = "ended"
