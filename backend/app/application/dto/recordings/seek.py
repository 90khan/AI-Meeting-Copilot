"""Immutable, privacy-safe V1 recording playback seek DTOs."""

from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolveRecordingSeekCommand:
    """Request resolution of one target time on a recording playback timeline."""

    meeting_id: MeetingId
    target_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.meeting_id, MeetingId)
            or not isinstance(self.target_seconds, (int, float))
            or not isfinite(self.target_seconds)
            or self.target_seconds < 0
        ):
            raise ApplicationValidationError("Recording seek target is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingSeekTarget:
    """Exact durable sample position inside a persisted V1 recording segment."""

    recording_id: UUID
    segment_index: int
    segment_start_sample: int
    segment_sample_count: int
    target_sample: int
    offset_samples: int
    resolved_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.recording_id, UUID)
            or type(self.segment_index) is not int
            or self.segment_index < 0
            or type(self.segment_start_sample) is not int
            or self.segment_start_sample < 0
            or type(self.segment_sample_count) is not int
            or self.segment_sample_count <= 0
            or type(self.target_sample) is not int
            or self.target_sample < 0
            or type(self.offset_samples) is not int
            or self.offset_samples < 0
            or self.offset_samples >= self.segment_sample_count
            or not isinstance(self.resolved_seconds, (int, float))
            or not isfinite(self.resolved_seconds)
            or self.resolved_seconds < 0
        ):
            raise ApplicationValidationError("Recording seek target is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingSeekResolution:
    """Represent either an in-segment position or the explicit playback end."""

    target: RecordingSeekTarget | None
    at_end: bool

    def __post_init__(self) -> None:
        if type(self.at_end) is not bool or (self.at_end != (self.target is None)):
            raise ApplicationValidationError("Recording seek resolution is invalid.")
