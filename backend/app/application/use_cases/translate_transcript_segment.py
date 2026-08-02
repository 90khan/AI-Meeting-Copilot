"""Transient translation of one persisted finalized transcript segment."""

from app.application.dto.ai import LanguageCode, TranslationRequest
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
    TranscriptSegment,
)
from app.application.exceptions import ProviderError
from app.application.interfaces import TranslationProvider


class TranslateTranscriptSegmentUseCase:
    """Translate one finalized segment without changing transcript persistence."""

    def __init__(
        self,
        *,
        provider: TranslationProvider,
        target_language: LanguageCode,
    ) -> None:
        """Initialize the explicit translation capability dependency."""

        self._provider = provider
        self._target_language = target_language

    async def execute(self, segment: TranscriptSegment) -> AssistUpdate:
        """Translate a finalized German segment or return a generic failed update."""

        try:
            result = await self._provider.translate(
                TranslationRequest(
                    text=segment.text,
                    source_language=LanguageCode(value="de"),
                    target_language=self._target_language,
                )
            )
        except ProviderError:
            return AssistUpdate(
                transcript_id=segment.transcript_id,
                capability=AssistCapability.TRANSLATION,
                state=AssistState.FAILED,
            )
        return AssistUpdate(
            transcript_id=segment.transcript_id,
            capability=AssistCapability.TRANSLATION,
            state=AssistState.READY,
            translated_text=result.translated_text,
        )
