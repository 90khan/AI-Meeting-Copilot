"""DTOs for reading public completed Meeting review artifacts."""

from dataclasses import dataclass

from app.application.dto.meeting_review.review_artifact import MeetingReviewArtifact
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMeetingReviewQuery:
    """Request the latest or one exact completed Meeting review version."""

    meeting_id: MeetingId
    version: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.meeting_id, MeetingId) or (
            self.version is not None
            and (type(self.version) is not int or self.version < 1)
        ):
            raise ApplicationValidationError("Meeting review query is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMeetingReviewResult:
    """Return one public-readable completed Meeting review artifact."""

    artifact: MeetingReviewArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, MeetingReviewArtifact):
            raise ApplicationValidationError("Meeting review result is invalid.")
