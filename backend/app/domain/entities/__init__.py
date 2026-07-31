"""Domain entity abstractions."""

from app.domain.entities.aggregate_root import AggregateRoot
from app.domain.entities.entity import Entity
from app.domain.entities.meeting import Meeting
from app.domain.entities.transcript_entry import TranscriptEntry

__all__ = ["AggregateRoot", "Entity", "Meeting", "TranscriptEntry"]
