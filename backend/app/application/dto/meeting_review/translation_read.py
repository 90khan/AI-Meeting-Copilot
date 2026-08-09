"""DTOs for reading a completed Turkish Meeting translation artifact."""

from dataclasses import dataclass

from app.application.dto.meeting_review.translation_artifact import (
    MeetingTranslationArtifact,
)
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMeetingTranslationQuery:
    """Request the latest or one exact version of a Meeting translation."""

    meeting_id: MeetingId
    version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.meeting_id, MeetingId) or (
            self.version is not None
            and (type(self.version) is not int or self.version < 1)
        ):
            raise ApplicationValidationError("Meeting translation query is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMeetingTranslationResult:
    """Return one public-readable Meeting translation artifact."""

    artifact: MeetingTranslationArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, MeetingTranslationArtifact):
            raise ApplicationValidationError("Meeting translation result is invalid.")
