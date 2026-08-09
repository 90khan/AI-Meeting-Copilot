"""Immutable detailed Meeting review read DTO."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.dto.meeting_review.transcript_read import TranscriptReadItem
from app.application.dto.recordings import RecordingState
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId, MeetingStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingDetail:
    meeting_id: MeetingId
    title: str
    status: MeetingStatus
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    transcript: tuple[TranscriptReadItem, ...]
    recording_available: bool
    recording_state: RecordingState | None
    recording_duration_seconds: float | None
    audio_expires_at: datetime | None
    audio_protected: bool
    audio_has_gaps: bool

    def __post_init__(self) -> None:
        values = (
            self.created_at,
            self.started_at,
            self.ended_at,
            self.audio_expires_at,
        )
        if (
            not isinstance(self.meeting_id, MeetingId)
            or not self.title.strip()
            or not isinstance(self.status, MeetingStatus)
            or not isinstance(self.transcript, tuple)
            or not all(isinstance(item, TranscriptReadItem) for item in self.transcript)
            or any(
                value is not None
                and (value.tzinfo is None or value.utcoffset() != timedelta(0))
                for value in values
            )
        ):
            raise ApplicationValidationError("Meeting detail is invalid.")
