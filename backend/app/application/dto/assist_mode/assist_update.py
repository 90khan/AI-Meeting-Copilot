"""Privacy-safe transient Assist Mode capability updates."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.application.dto.ai import ReplySuggestion
from app.application.exceptions import ApplicationValidationError


class AssistCapability(StrEnum):
    """Independent enrichment capability associated with an update."""

    TRANSLATION = "translation"
    SIMPLIFICATION = "simplification"
    REPLY_COACHING = "reply_coaching"


class AssistState(StrEnum):
    """Privacy-safe lifecycle state for transient enrichment."""

    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True, kw_only=True)
class AssistUpdate:
    """One transient capability result correlated to a persisted transcript."""

    transcript_id: UUID
    capability: AssistCapability
    state: AssistState
    translated_text: str | None = None
    simplified_text: str | None = None
    reply_suggestions: tuple[ReplySuggestion, ...] | None = None

    def __post_init__(self) -> None:
        """Validate typed correlation and capability-specific ready payloads."""

        if not isinstance(self.transcript_id, UUID):
            raise ApplicationValidationError("Transcript ID must be a UUID.")
        if not isinstance(self.capability, AssistCapability):
            raise ApplicationValidationError("Assist capability is invalid.")
        if not isinstance(self.state, AssistState):
            raise ApplicationValidationError("Assist state is invalid.")
        for value, field_name in (
            (self.translated_text, "Translated text"),
            (self.simplified_text, "Simplified text"),
        ):
            if value is not None and not value.strip():
                raise ApplicationValidationError(f"{field_name} must not be blank.")
        if self.reply_suggestions is not None:
            if not self.reply_suggestions:
                raise ApplicationValidationError("Reply suggestions must not be empty.")
            if len(self.reply_suggestions) > 2:
                raise ApplicationValidationError(
                    "Reply suggestions must not exceed two entries."
                )
        if self.state is AssistState.READY:
            has_expected_payload = {
                AssistCapability.TRANSLATION: self.translated_text is not None,
                AssistCapability.SIMPLIFICATION: self.simplified_text is not None,
                AssistCapability.REPLY_COACHING: self.reply_suggestions is not None,
            }[self.capability]
            if not has_expected_payload:
                raise ApplicationValidationError(
                    "A ready Assist update requires its capability result."
                )
