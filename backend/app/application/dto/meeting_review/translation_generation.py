"""DTOs for in-memory Meeting translation artifact generation."""

from dataclasses import dataclass

from app.application.dto.meeting_review.translation_artifact import (
    MeetingTranslationArtifact,
)
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateMeetingTranslationCommand:
    """Request generation of a versioned Turkish Meeting translation."""

    meeting_id: MeetingId
    force_regenerate: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateMeetingTranslationResult:
    """Return an in-memory artifact and whether an existing one was reused."""

    artifact: MeetingTranslationArtifact
    reused_existing: bool
