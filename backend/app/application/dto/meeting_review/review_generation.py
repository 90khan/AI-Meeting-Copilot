"""Data transfer objects for structured hierarchical Meeting review generation."""

from dataclasses import dataclass
from enum import StrEnum

from app.application.dto.meeting_review.review_artifact import (
    MeetingReviewArtifact,
    MeetingReviewContent,
)
from app.application.dto.meeting_review.review_batch import TranscriptReviewBatch
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


class MeetingReviewGenerationStage(StrEnum):
    """The two structured-generation stages in the hierarchical workflow."""

    BATCH = "batch"
    REDUCE = "reduce"


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateMeetingReviewCommand:
    """Request in-memory review generation for one ended Meeting."""

    meeting_id: MeetingId
    force_regenerate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.meeting_id, MeetingId) or not isinstance(
            self.force_regenerate, bool
        ):
            raise ApplicationValidationError(
                "Meeting review generation command is invalid."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateMeetingReviewResult:
    """One persisted review artifact with explicit conservative reuse information."""

    artifact: MeetingReviewArtifact
    reused_existing: bool

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, MeetingReviewArtifact) or not isinstance(
            self.reused_existing, bool
        ):
            raise ApplicationValidationError(
                "Meeting review generation result is invalid."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingReviewGenerationRequest:
    """Structured provider input containing batch text or merged review content only."""

    meeting_id: MeetingId
    stage: MeetingReviewGenerationStage
    batch: TranscriptReviewBatch | None
    intermediate_content: MeetingReviewContent | None

    def __post_init__(self) -> None:
        is_batch_request = (
            self.stage is MeetingReviewGenerationStage.BATCH
            and isinstance(self.batch, TranscriptReviewBatch)
            and self.intermediate_content is None
        )
        is_reduce_request = (
            self.stage is MeetingReviewGenerationStage.REDUCE
            and self.batch is None
            and isinstance(self.intermediate_content, MeetingReviewContent)
        )
        if (
            not isinstance(self.meeting_id, MeetingId)
            or not isinstance(self.stage, MeetingReviewGenerationStage)
            or not (is_batch_request or is_reduce_request)
        ):
            raise ApplicationValidationError(
                "Meeting review generation request is invalid."
            )
