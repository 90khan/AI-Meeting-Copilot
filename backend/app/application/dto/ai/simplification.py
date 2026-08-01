"""German simplification application data transfer objects."""

from dataclasses import dataclass
from enum import StrEnum

from app.application.exceptions import ApplicationValidationError


class GermanLevel(StrEnum):
    """Supported German proficiency levels for text simplification."""

    A2 = "a2"
    B1 = "b1"
    B2 = "b2"
    C1 = "c1"


@dataclass(frozen=True, slots=True, kw_only=True)
class GermanSimplificationRequest:
    """Input supplied to a German simplification capability."""

    text: str
    target_level: GermanLevel
    preserve_technical_terms: bool = True

    def __post_init__(self) -> None:
        """Validate the text that will be simplified."""

        if not self.text.strip():
            raise ApplicationValidationError(
                "German simplification text must not be blank."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class GermanSimplificationResult:
    """The original and simplified German text."""

    original_text: str
    simplified_text: str
    target_level: GermanLevel

    def __post_init__(self) -> None:
        """Validate both forms of the simplified text."""

        if not self.original_text.strip():
            raise ApplicationValidationError("Original text must not be blank.")
        if not self.simplified_text.strip():
            raise ApplicationValidationError("Simplified text must not be blank.")
