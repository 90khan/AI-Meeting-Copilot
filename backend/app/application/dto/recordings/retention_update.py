"""Privacy-safe commands and results for recording retention updates."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.application.dto.recordings.recording import RecordingDeletionStatus
from app.application.dto.recordings.retention import RecordingRetentionPolicy
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_INVALID_COMMAND = "Audio retention update command is invalid."
_INVALID_RESULT = "Audio retention update result is invalid."


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateAudioRetentionCommand:
    """Update retention scheduling without touching audio or encryption material."""

    meeting_id: MeetingId
    retention_policy: RecordingRetentionPolicy
    protected: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.meeting_id, MeetingId)
            or not isinstance(self.retention_policy, RecordingRetentionPolicy)
            or not isinstance(self.protected, bool)
        ):
            raise ApplicationValidationError(_INVALID_COMMAND)


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateAudioRetentionResult:
    """The persisted, privacy-safe retention scheduling state."""

    meeting_id: MeetingId
    recording_id: UUID
    retention_policy: RecordingRetentionPolicy
    expires_at: datetime | None
    protected: bool
    deletion_status: RecordingDeletionStatus

    def __post_init__(self) -> None:
        if (
            not isinstance(self.meeting_id, MeetingId)
            or not isinstance(self.recording_id, UUID)
            or not isinstance(self.retention_policy, RecordingRetentionPolicy)
            or not isinstance(self.protected, bool)
            or not isinstance(self.deletion_status, RecordingDeletionStatus)
        ):
            raise ApplicationValidationError(_INVALID_RESULT)
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() != timedelta(0)
        ):
            raise ApplicationValidationError(_INVALID_RESULT)
