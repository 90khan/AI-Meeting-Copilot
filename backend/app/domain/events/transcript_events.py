"""Domain events emitted for Meeting transcript changes."""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.domain.events.domain_event import DomainEvent
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptAdded(DomainEvent[MeetingId]):
    """A transcript entry was added to a Meeting."""

    transcript_id: UUID
    speaker: str

    EVENT_TYPE: ClassVar[str] = "transcript.added"
