"""Internal immutable DTOs for trusted local recording playback."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from uuid import UUID

from app.application.dto.recordings.recording import RecordingMediaFormat
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingPlaybackInfo:
    """Safe recording metadata required to initialize local playback."""

    recording_id: UUID
    meeting_id: MeetingId
    format: RecordingMediaFormat
    capture_anchor_utc: datetime
    duration_seconds: float | None
    segment_count: int
    has_gaps: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.recording_id, UUID)
            or not isinstance(self.meeting_id, MeetingId)
            or not isinstance(self.format, RecordingMediaFormat)
            or not self.format.is_playback_supported
            or not isinstance(self.capture_anchor_utc, datetime)
            or self.capture_anchor_utc.tzinfo is None
            or self.capture_anchor_utc.utcoffset() != timedelta(0)
            or type(self.segment_count) is not int
            or self.segment_count < 0
            or (
                self.duration_seconds is not None
                and (not isfinite(self.duration_seconds) or self.duration_seconds < 0)
            )
        ):
            raise ApplicationValidationError("Recording playback metadata is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingPlaybackSegment:
    """One decrypted in-memory segment; never suitable for public serialization."""

    segment_index: int
    plaintext_audio: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.segment_index) is not int
            or self.segment_index < 0
            or not isinstance(self.plaintext_audio, bytes)
            or not self.plaintext_audio
        ):
            raise ApplicationValidationError("Recording playback segment is invalid.")
