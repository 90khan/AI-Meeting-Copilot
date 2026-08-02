"""Stable finalized transcript data supplied to Assist Mode capabilities."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptSegment:
    """One persisted finalized transcript segment eligible for enrichment."""

    transcript_id: UUID
    meeting_id: MeetingId
    text: str
    timestamp: datetime
    source: AudioSource
    speaker: str

    def __post_init__(self) -> None:
        """Validate stable identity, finalized content, and UTC metadata."""

        if not isinstance(self.transcript_id, UUID):
            raise ApplicationValidationError("Transcript ID must be a UUID.")
        if not isinstance(self.meeting_id, MeetingId):
            raise ApplicationValidationError("Meeting ID must be a MeetingId.")
        if not self.text.strip():
            raise ApplicationValidationError("Transcript text must not be blank.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ApplicationValidationError(
                "Transcript timestamp must be timezone-aware."
            )
        if self.timestamp.utcoffset() != timedelta(0):
            raise ApplicationValidationError("Transcript timestamp must use UTC.")
        if not isinstance(self.source, AudioSource):
            raise ApplicationValidationError("Transcript source is invalid.")
        if not self.speaker.strip():
            raise ApplicationValidationError("Transcript speaker must not be blank.")
