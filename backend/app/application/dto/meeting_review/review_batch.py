"""Immutable DTOs for deterministic hierarchical Meeting review work."""

from dataclasses import dataclass
from uuid import UUID

from app.application.dto.meeting_review.review_artifact import MeetingReviewContent
from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptReviewBatch:
    """One ordered, whole-segment transcript batch for review generation."""

    batch_index: int
    start_transcript_index: int
    end_transcript_index: int
    transcript_ids: tuple[UUID, ...]
    texts: tuple[str, ...]
    total_characters: int

    def __post_init__(self) -> None:
        if (
            type(self.batch_index) is not int
            or self.batch_index < 0
            or type(self.start_transcript_index) is not int
            or self.start_transcript_index < 0
            or type(self.end_transcript_index) is not int
            or self.end_transcript_index < self.start_transcript_index
            or not isinstance(self.transcript_ids, tuple)
            or not isinstance(self.texts, tuple)
            or not self.transcript_ids
            or len(self.transcript_ids) != len(self.texts)
            or not all(
                isinstance(transcript_id, UUID) for transcript_id in self.transcript_ids
            )
            or not all(isinstance(text, str) and text.strip() for text in self.texts)
            or type(self.total_characters) is not int
            or self.total_characters < 0
            or self.total_characters != sum(len(text) for text in self.texts)
        ):
            raise ApplicationValidationError("Transcript review batch is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class BatchReviewResult:
    """One already-generated content result, identified only by batch order."""

    batch_index: int
    content: MeetingReviewContent

    def __post_init__(self) -> None:
        if (
            type(self.batch_index) is not int
            or self.batch_index < 0
            or not isinstance(self.content, MeetingReviewContent)
        ):
            raise ApplicationValidationError("Batch review result is invalid.")
