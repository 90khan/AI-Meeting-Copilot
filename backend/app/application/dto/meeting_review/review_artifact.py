"""Immutable, versioned Meeting interview-review artifact DTOs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_FAILURE_CODES = frozenset(
    {
        "review_provider_failed",
        "review_generation_failed",
        "review_cancelled",
    }
)


class MeetingReviewArtifactStatus(StrEnum):
    """Lifecycle states for a versioned Meeting review artifact."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewActionItem:
    """One generated action item with optional ownership and due-date text."""

    text: str
    owner: str | None
    due_date: str | None

    def __post_init__(self) -> None:
        if (
            not _is_nonblank_string(self.text)
            or not _is_optional_nonblank_string(self.owner)
            or not _is_optional_nonblank_string(self.due_date)
        ):
            raise ApplicationValidationError("Review action item is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewOpenQuestion:
    """One unresolved question identified by the review."""

    question: str

    def __post_init__(self) -> None:
        if not _is_nonblank_string(self.question):
            raise ApplicationValidationError("Review open question is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewTechnicalTerm:
    """One technical term and its generated explanation."""

    term: str
    explanation: str

    def __post_init__(self) -> None:
        if not _is_nonblank_string(self.term) or not _is_nonblank_string(
            self.explanation
        ):
            raise ApplicationValidationError("Review technical term is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewInterviewQuestion:
    """One interview question with optional answer and coaching details."""

    question: str
    answer_summary: str | None
    evaluation: str | None
    improvement_suggestion: str | None

    def __post_init__(self) -> None:
        if (
            not _is_nonblank_string(self.question)
            or not _is_optional_nonblank_string(self.answer_summary)
            or not _is_optional_nonblank_string(self.evaluation)
            or not _is_optional_nonblank_string(self.improvement_suggestion)
        ):
            raise ApplicationValidationError("Review interview question is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewFeedback:
    """Generated aggregate feedback without any provider implementation details."""

    strengths: tuple[str, ...]
    improvement_areas: tuple[str, ...]
    overall_feedback: str

    def __post_init__(self) -> None:
        if (
            not _is_tuple_of_nonblank_strings(self.strengths)
            or not _is_tuple_of_nonblank_strings(self.improvement_areas)
            or not _is_nonblank_string(self.overall_feedback)
        ):
            raise ApplicationValidationError("Review feedback is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingReviewContent:
    """Generated product review content, excluding transcript and provider payloads."""

    summary: str
    key_decisions: tuple[str, ...]
    action_items: tuple[ReviewActionItem, ...]
    open_questions: tuple[ReviewOpenQuestion, ...]
    technical_questions: tuple[ReviewInterviewQuestion, ...]
    technical_terms: tuple[ReviewTechnicalTerm, ...]
    feedback: ReviewFeedback | None

    def __post_init__(self) -> None:
        if (
            not _is_nonblank_string(self.summary)
            or not _is_tuple_of_nonblank_strings(self.key_decisions)
            or not _is_tuple_of(self.action_items, ReviewActionItem)
            or not _is_tuple_of(self.open_questions, ReviewOpenQuestion)
            or not _is_tuple_of(self.technical_questions, ReviewInterviewQuestion)
            or not _is_tuple_of(self.technical_terms, ReviewTechnicalTerm)
            or (
                self.feedback is not None
                and not isinstance(self.feedback, ReviewFeedback)
            )
        ):
            raise ApplicationValidationError("Meeting review content is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingReviewArtifact:
    """A versioned, immutable generated interview review for one Meeting."""

    artifact_id: UUID
    meeting_id: MeetingId
    version: int
    review_type: str
    status: MeetingReviewArtifactStatus
    created_at: datetime
    completed_at: datetime | None
    source_transcript_count: int
    content: MeetingReviewContent | None
    provider_name: str
    model_name: str
    prompt_version: str
    schema_version: int
    failure_code: str | None

    def __post_init__(self) -> None:
        if not self._has_valid_general_fields():
            raise ApplicationValidationError("Meeting review artifact is invalid.")

        if self.status is MeetingReviewArtifactStatus.COMPLETED:
            is_consistent = (
                self.completed_at is not None
                and self.content is not None
                and self.failure_code is None
            )
        elif self.status in {
            MeetingReviewArtifactStatus.PENDING,
            MeetingReviewArtifactStatus.PROCESSING,
        }:
            is_consistent = (
                self.completed_at is None
                and self.content is None
                and self.failure_code is None
            )
        elif self.status is MeetingReviewArtifactStatus.FAILED:
            is_consistent = (
                self.completed_at is None
                and self.content is None
                and _is_failure_code(self.failure_code)
            )
        else:
            is_consistent = (
                self.completed_at is None
                and self.content is None
                and self.failure_code in {None, "review_cancelled"}
            )

        if not is_consistent:
            raise ApplicationValidationError("Meeting review artifact is inconsistent.")

    def _has_valid_general_fields(self) -> bool:
        return (
            isinstance(self.artifact_id, UUID)
            and isinstance(self.meeting_id, MeetingId)
            and type(self.version) is int
            and self.version >= 1
            and self.review_type == "interview_review"
            and isinstance(self.status, MeetingReviewArtifactStatus)
            and _is_utc(self.created_at)
            and (self.completed_at is None or _is_utc(self.completed_at))
            and type(self.source_transcript_count) is int
            and self.source_transcript_count >= 0
            and (self.content is None or isinstance(self.content, MeetingReviewContent))
            and _is_nonblank_string(self.provider_name)
            and _is_nonblank_string(self.model_name)
            and _is_nonblank_string(self.prompt_version)
            and type(self.schema_version) is int
            and self.schema_version >= 1
            and (self.failure_code is None or _is_failure_code(self.failure_code))
        )


def _is_nonblank_string(value: object) -> bool:
    """Return whether a runtime value is non-blank without altering it."""

    return isinstance(value, str) and bool(value.strip())


def _is_optional_nonblank_string(value: object) -> bool:
    """Return whether an optional human-readable value is valid."""

    return value is None or _is_nonblank_string(value)


def _is_tuple_of_nonblank_strings(value: object) -> bool:
    """Return whether a runtime value is an immutable string collection."""

    return isinstance(value, tuple) and all(_is_nonblank_string(item) for item in value)


def _is_tuple_of(value: object, item_type: type[object]) -> bool:
    """Return whether a runtime value is a tuple of a required DTO type."""

    return isinstance(value, tuple) and all(
        isinstance(item, item_type) for item in value
    )


def _is_utc(value: object) -> bool:
    """Return whether a runtime value is a timezone-aware UTC datetime."""

    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _is_failure_code(value: object) -> bool:
    """Return whether a runtime value is an approved generic failure code."""

    return isinstance(value, str) and value in _FAILURE_CODES
