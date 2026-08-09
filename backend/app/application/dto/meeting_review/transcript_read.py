"""Immutable read DTOs for persisted transcript entries."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.application.dto import AudioSource
from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptReadItem:
    transcript_id: UUID
    text: str
    timestamp: datetime
    speaker: str
    source: AudioSource

    def __post_init__(self) -> None:
        if (
            not isinstance(self.transcript_id, UUID)
            or not self.text.strip()
            or not self.speaker.strip()
            or not isinstance(self.source, AudioSource)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() != timedelta(0)
        ):
            raise ApplicationValidationError("Transcript read item is invalid.")
