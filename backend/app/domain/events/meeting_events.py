"""Domain events emitted by the Meeting aggregate."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from app.domain.events.domain_event import DomainEvent
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingCreated(DomainEvent[MeetingId]):
    """A Meeting aggregate was created."""

    meeting_name: str

    EVENT_TYPE: ClassVar[str] = "meeting.created"


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingStarted(DomainEvent[MeetingId]):
    """A Meeting aggregate became active."""

    started_at: datetime

    EVENT_TYPE: ClassVar[str] = "meeting.started"


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingEnded(DomainEvent[MeetingId]):
    """A Meeting aggregate ended."""

    ended_at: datetime

    EVENT_TYPE: ClassVar[str] = "meeting.ended"


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingRenamed(DomainEvent[MeetingId]):
    """A Meeting aggregate changed its name."""

    old_name: str
    new_name: str

    EVENT_TYPE: ClassVar[str] = "meeting.renamed"
