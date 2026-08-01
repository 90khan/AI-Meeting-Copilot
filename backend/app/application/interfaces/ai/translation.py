"""Translation provider contract."""

from typing import Protocol, runtime_checkable

from app.application.dto.ai.translation import TranslationRequest, TranslationResult


@runtime_checkable
class TranslationProvider(Protocol):
    """Provides asynchronous text translation."""

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate a complete request into its target language."""
