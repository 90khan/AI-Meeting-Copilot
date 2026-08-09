"""Immutable Meeting history read DTO."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.dto.recordings import RecordingState
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId, MeetingStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingHistoryItem:
    meeting_id: MeetingId
    title: str
    status: MeetingStatus
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    transcript_count: int
    recording_available: bool
    recording_state: RecordingState | None
    audio_expires_at: datetime | None
    audio_protected: bool

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
            or self.transcript_count < 0
            or not isinstance(self.recording_available, bool)
            or not isinstance(self.audio_protected, bool)
            or any(
                value is not None
                and (value.tzinfo is None or value.utcoffset() != timedelta(0))
                for value in values
            )
        ):
            raise ApplicationValidationError("Meeting history item is invalid.")
