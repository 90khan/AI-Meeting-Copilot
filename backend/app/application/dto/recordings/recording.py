"""Immutable recording metadata safe for application-layer use."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from uuid import UUID

from app.application.dto.recordings.retention import RecordingRetentionPolicy
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


class RecordingState(StrEnum):
    """Lifecycle state of a local recording."""

    PENDING = "pending"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    DELETED = "deleted"
    MISSING = "missing"


class RecordingDeletionStatus(StrEnum):
    """State of the separate local-file deletion workflow."""

    NOT_SCHEDULED = "not_scheduled"
    SCHEDULED = "scheduled"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingMetadata:
    """Public recording metadata without storage paths or key material."""

    recording_id: UUID
    meeting_id: MeetingId
    state: RecordingState
    retention_policy: RecordingRetentionPolicy
    created_at: datetime
    expires_at: datetime | None
    capture_anchor_utc: datetime | None
    duration_seconds: float | None
    protected: bool
    consent_confirmed: bool
    consent_confirmed_at: datetime | None
    deletion_status: RecordingDeletionStatus
    deleted_at: datetime | None
    encryption_format_version: int
    container_format: str
    segment_count: int
    has_gaps: bool

    def __post_init__(self) -> None:
        """Validate lifecycle and privacy-safe metadata invariants."""

        for value in (
            self.created_at,
            self.expires_at,
            self.capture_anchor_utc,
            self.consent_confirmed_at,
            self.deleted_at,
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() != timedelta(0)
            ):
                raise ApplicationValidationError("Recording timestamps must use UTC.")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ApplicationValidationError("Recording duration is invalid.")
        if self.encryption_format_version <= 0 or self.segment_count < 0:
            raise ApplicationValidationError("Recording metadata is invalid.")
        if self.container_format != "m4a":
            raise ApplicationValidationError(
                "Recording container format is not supported."
            )
        if self.consent_confirmed != (self.consent_confirmed_at is not None):
            raise ApplicationValidationError(
                "Recording consent metadata is inconsistent."
            )
        if self.state is RecordingState.RECORDING and not self.consent_confirmed:
            raise ApplicationValidationError("Recording requires confirmed consent.")
        if self.retention_policy is RecordingRetentionPolicy.MANUAL:
            if self.expires_at is not None:
                raise ApplicationValidationError(
                    "Manual retention cannot expire automatically."
                )
        elif self.expires_at is None or self.expires_at <= self.created_at:
            raise ApplicationValidationError(
                "Timed retention requires a future expiry."
            )
        if self.state is RecordingState.DELETED:
            if (
                self.deletion_status is not RecordingDeletionStatus.DELETED
                or self.deleted_at is None
            ):
                raise ApplicationValidationError(
                    "Deleted recording metadata is inconsistent."
                )
        elif self.deletion_status is RecordingDeletionStatus.DELETED:
            raise ApplicationValidationError(
                "Only deleted recordings may be marked deleted."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingMetadataRecord:
    """Internal persistence record with opaque storage and Keychain references."""

    metadata: RecordingMetadata
    storage_directory_token: str
    key_reference: str
    current_segment_index: int
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Reject malformed opaque persistence fields."""

        if not self.storage_directory_token.strip() or not self.key_reference.strip():
            raise ApplicationValidationError("Recording storage metadata is invalid.")
        if self.current_segment_index < 0:
            raise ApplicationValidationError("Recording segment metadata is invalid.")
        if self.failure_code is not None and not self.failure_code.strip():
            raise ApplicationValidationError("Recording failure metadata is invalid.")
