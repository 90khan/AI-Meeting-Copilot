"""Safe recording-segment metadata exposed above infrastructure."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingSegmentDescriptor:
    """Metadata for a completed encrypted recording segment."""

    recording_id: UUID
    segment_index: int
    plaintext_length: int
    ciphertext_length: int
    created_at: datetime
    completed: bool
    format_version: int

    def __post_init__(self) -> None:
        if (
            self.segment_index < 0
            or self.plaintext_length < 0
            or self.ciphertext_length < 0
        ):
            raise ApplicationValidationError("Recording segment metadata is invalid.")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(
            0
        ):
            raise ApplicationValidationError(
                "Recording segment timestamps must use UTC."
            )
        if self.format_version <= 0:
            raise ApplicationValidationError("Recording segment metadata is invalid.")
