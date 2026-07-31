"""Domain event abstractions."""

from app.domain.events.domain_event import DomainEvent
from app.domain.events.meeting_events import (
    MeetingCreated,
    MeetingEnded,
    MeetingRenamed,
    MeetingStarted,
)

__all__ = [
    "DomainEvent",
    "MeetingCreated",
    "MeetingEnded",
    "MeetingRenamed",
    "MeetingStarted",
]
