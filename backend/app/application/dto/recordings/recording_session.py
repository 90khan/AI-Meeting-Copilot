"""Commands and results for recording metadata lifecycle transitions."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from re import fullmatch
from typing import ClassVar
from uuid import UUID

from app.application.dto.recordings.retention import RecordingRetentionPolicy
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ApplicationValidationError("Recording timestamps must use UTC.")


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareRecordingSessionCommand:
    """Request optional recording metadata preparation for an active Meeting."""

    meeting_id: MeetingId
    recording_enabled: bool
    retention_policy: RecordingRetentionPolicy
    consent_confirmed: bool
    consent_confirmed_at: datetime | None
    protected: bool = False

    def __post_init__(self) -> None:
        if self.consent_confirmed:
            if self.consent_confirmed_at is None:
                raise ApplicationValidationError(
                    "Recording consent requires a timestamp."
                )
            _require_utc(self.consent_confirmed_at)
        elif self.consent_confirmed_at is not None:
            raise ApplicationValidationError(
                "Recording consent metadata is inconsistent."
            )
        if self.recording_enabled and not self.consent_confirmed:
            raise ApplicationValidationError("Recording requires confirmed consent.")


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareRecordingSessionResult:
    """Return safe metadata needed to continue a configured session."""

    enabled: bool
    recording_id: UUID | None
    meeting_id: MeetingId
    retention_policy: RecordingRetentionPolicy | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkRecordingStartedCommand:
    """Mark prepared metadata as actively recording."""

    recording_id: UUID
    capture_anchor_utc: datetime

    def __post_init__(self) -> None:
        _require_utc(self.capture_anchor_utc)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinalizeRecordingCommand:
    """Record final safe metadata after local writer finalization."""

    recording_id: UUID
    duration_seconds: float
    segment_count: int
    has_gaps: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkRecordingFailedCommand:
    """Record a short, generic failure code without native details."""

    _ALLOWED_FAILURE_CODES: ClassVar[frozenset[str]] = frozenset(
        {
            "capture_failed",
            "encryption_failed",
            "finalization_failed",
            "interrupted",
            "storage_failed",
            "writer_failed",
        }
    )

    recording_id: UUID
    failure_code: str

    def __post_init__(self) -> None:
        if (
            fullmatch(r"[a-z0-9_]{1,64}", self.failure_code) is None
            or self.failure_code not in self._ALLOWED_FAILURE_CODES
        ):
            raise ApplicationValidationError("Recording failure code is invalid.")
