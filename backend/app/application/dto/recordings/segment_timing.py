"""Immutable playback-timeline metadata for one retained recording segment."""

from dataclasses import dataclass
from uuid import UUID

from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingSegmentTiming:
    recording_id: UUID
    segment_index: int
    sample_count: int
    start_sample: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.recording_id, UUID)
            or type(self.segment_index) is not int
            or self.segment_index < 0
            or type(self.sample_count) is not int
            or self.sample_count <= 0
            or type(self.start_sample) is not int
            or self.start_sample < 0
        ):
            raise ApplicationValidationError("Recording segment timing is invalid.")
