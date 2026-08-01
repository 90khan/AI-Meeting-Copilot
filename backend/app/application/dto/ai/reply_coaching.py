"""Reply-coaching application data transfer objects."""

from dataclasses import dataclass
from enum import StrEnum

from app.application.dto.ai.language import LanguageCode
from app.application.dto.ai.simplification import GermanLevel
from app.application.exceptions import ApplicationValidationError


class ReplyTone(StrEnum):
    """Supported tones for suggested replies."""

    NEUTRAL = "neutral"
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    CONFIDENT = "confident"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplyCoachingRequest:
    """Input supplied to a reply-coaching capability."""

    conversation_context: str
    latest_utterance: str
    response_language: LanguageCode
    target_german_level: GermanLevel | None = None
    tone: ReplyTone = ReplyTone.PROFESSIONAL
    max_suggestions: int = 2

    def __post_init__(self) -> None:
        """Validate context, response preferences, and suggestion count."""

        if not self.conversation_context.strip():
            raise ApplicationValidationError("Conversation context must not be blank.")
        if not self.latest_utterance.strip():
            raise ApplicationValidationError("Latest utterance must not be blank.")
        if not 1 <= self.max_suggestions <= 3:
            raise ApplicationValidationError(
                "Maximum suggestions must be between 1 and 3."
            )
        if (
            self.target_german_level is not None
            and self.response_language.value.split("-", maxsplit=1)[0] != "de"
        ):
            raise ApplicationValidationError(
                "A target German level requires a German response language."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplySuggestion:
    """One reply proposed by the coaching capability."""

    text: str
    tone: ReplyTone

    def __post_init__(self) -> None:
        """Validate the suggested reply text."""

        if not self.text.strip():
            raise ApplicationValidationError("Reply suggestion text must not be blank.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplyCoachingResult:
    """A bounded collection of coached reply suggestions."""

    suggestions: tuple[ReplySuggestion, ...]

    def __post_init__(self) -> None:
        """Validate the required, bounded suggestion collection."""

        if not self.suggestions:
            raise ApplicationValidationError(
                "Reply coaching suggestions must not be empty."
            )
        if len(self.suggestions) > 3:
            raise ApplicationValidationError(
                "Reply coaching suggestions must not exceed three entries."
            )
