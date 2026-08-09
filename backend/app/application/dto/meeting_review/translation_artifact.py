"""Immutable, versioned Turkish Meeting translation artifact DTOs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId

_FAILURE_CODES = frozenset(
    {
        "translation_provider_failed",
        "translation_cancelled",
    }
)
_MAX_GENERATION_IDENTIFIER_LENGTH = 120


class TranslationArtifactStatus(StrEnum):
    """Lifecycle states for a versioned Meeting translation artifact."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationArtifactSegment:
    """One finalized transcript segment and its Turkish translation."""

    transcript_id: UUID
    source_text: str
    translated_text: str

    def __post_init__(self) -> None:
        """Validate the immutable segment without rewriting its text."""

        if not isinstance(self.transcript_id, UUID):
            raise ApplicationValidationError("Translation artifact segment is invalid.")
        if not isinstance(self.source_text, str) or not self.source_text.strip():
            raise ApplicationValidationError("Translation artifact segment is invalid.")
        if (
            not isinstance(self.translated_text, str)
            or not self.translated_text.strip()
        ):
            raise ApplicationValidationError("Translation artifact segment is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class MeetingTranslationArtifact:
    """A versioned, auditable Turkish translation of a Meeting transcript."""

    artifact_id: UUID
    meeting_id: MeetingId
    version: int
    target_language: str
    status: TranslationArtifactStatus
    created_at: datetime
    completed_at: datetime | None
    source_transcript_count: int
    segments: tuple[TranslationArtifactSegment, ...]
    provider_name: str
    model_name: str
    prompt_version: str
    schema_version: int
    failure_code: str | None

    def __post_init__(self) -> None:
        """Enforce lifecycle-specific artifact invariants."""

        if not self._has_valid_general_fields():
            raise ApplicationValidationError("Meeting translation artifact is invalid.")

        if self.status is TranslationArtifactStatus.COMPLETED:
            is_consistent = (
                self.completed_at is not None
                and self.failure_code is None
                and len(self.segments) == self.source_transcript_count
            )
        elif self.status in {
            TranslationArtifactStatus.PENDING,
            TranslationArtifactStatus.PROCESSING,
        }:
            is_consistent = (
                self.completed_at is None
                and not self.segments
                and self.failure_code is None
            )
        elif self.status is TranslationArtifactStatus.FAILED:
            is_consistent = (
                self.completed_at is None
                and not self.segments
                and _is_failure_code(self.failure_code)
            )
        else:
            is_consistent = (
                self.completed_at is None
                and not self.segments
                and self.failure_code in {None, "translation_cancelled"}
            )

        if not is_consistent:
            raise ApplicationValidationError(
                "Meeting translation artifact is inconsistent."
            )

    def _has_valid_general_fields(self) -> bool:
        return (
            isinstance(self.artifact_id, UUID)
            and isinstance(self.meeting_id, MeetingId)
            and type(self.version) is int
            and self.version >= 1
            and self.target_language == "tr"
            and isinstance(self.status, TranslationArtifactStatus)
            and _is_utc(self.created_at)
            and (self.completed_at is None or _is_utc(self.completed_at))
            and type(self.source_transcript_count) is int
            and self.source_transcript_count >= 0
            and isinstance(self.segments, tuple)
            and all(
                isinstance(segment, TranslationArtifactSegment)
                for segment in self.segments
            )
            and all(
                _is_generation_identifier(value)
                for value in (
                    self.provider_name,
                    self.model_name,
                    self.prompt_version,
                )
            )
            and type(self.schema_version) is int
            and self.schema_version >= 1
            and (self.failure_code is None or _is_failure_code(self.failure_code))
        )


def _is_utc(value: object) -> bool:
    """Return whether a runtime value is a timezone-aware UTC datetime."""

    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _is_generation_identifier(value: object) -> bool:
    """Return whether metadata is a short, non-blank identifier."""

    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= _MAX_GENERATION_IDENTIFIER_LENGTH
    )


def _is_failure_code(value: object) -> bool:
    """Return whether a runtime value is an approved generic failure code."""

    return isinstance(value, str) and value in _FAILURE_CODES
