"""Privacy-safe commands and results for immediate recording deletion."""

from dataclasses import dataclass
from uuid import UUID

from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_INVALID_COMMAND = "Meeting audio deletion command is invalid."
_INVALID_RESULT = "Meeting audio deletion result is invalid."


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteMeetingAudioCommand:
    """Request deletion of the local recording associated with one Meeting."""

    meeting_id: MeetingId

    def __post_init__(self) -> None:
        if not isinstance(self.meeting_id, MeetingId):
            raise ApplicationValidationError(_INVALID_COMMAND)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteMeetingAudioResult:
    """Privacy-safe outcome for a successful or already-absent audio deletion."""

    meeting_id: MeetingId
    recording_id: UUID | None
    deleted: bool
    already_absent: bool

    def __post_init__(self) -> None:
        if not isinstance(self.meeting_id, MeetingId):
            raise ApplicationValidationError(_INVALID_RESULT)
        if self.recording_id is not None and not isinstance(self.recording_id, UUID):
            raise ApplicationValidationError(_INVALID_RESULT)
        if not isinstance(self.deleted, bool) or not isinstance(
            self.already_absent, bool
        ):
            raise ApplicationValidationError(_INVALID_RESULT)
        if self.deleted and (self.already_absent or self.recording_id is None):
            raise ApplicationValidationError(_INVALID_RESULT)
