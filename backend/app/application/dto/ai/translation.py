"""Translation application data transfer objects."""

from dataclasses import dataclass

from app.application.dto.ai.language import LanguageCode
from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationRequest:
    """Input supplied to a translation capability."""

    text: str
    source_language: LanguageCode | None
    target_language: LanguageCode
    preserve_formatting: bool = True

    def __post_init__(self) -> None:
        """Validate the translation input and language direction."""

        if not self.text.strip():
            raise ApplicationValidationError("Translation text must not be blank.")
        if self.source_language == self.target_language:
            raise ApplicationValidationError(
                "Translation source and target languages must differ."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationResult:
    """The translated text and detected source/target languages."""

    translated_text: str
    source_language: LanguageCode
    target_language: LanguageCode

    def __post_init__(self) -> None:
        """Validate the translated text and language direction."""

        if not self.translated_text.strip():
            raise ApplicationValidationError("Translated text must not be blank.")
        if self.source_language == self.target_language:
            raise ApplicationValidationError(
                "Translation source and target languages must differ."
            )
