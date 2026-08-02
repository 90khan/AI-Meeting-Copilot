"""Transient German simplification of one persisted transcript segment."""

from app.application.dto.ai import GermanLevel, GermanSimplificationRequest
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
    TranscriptSegment,
)
from app.application.exceptions import ProviderError
from app.application.interfaces import GermanSimplificationProvider


class SimplifyTranscriptSegmentUseCase:
    """Simplify one finalized segment without mutating the Meeting aggregate."""

    def __init__(self, *, provider: GermanSimplificationProvider) -> None:
        """Initialize the explicit simplification capability dependency."""

        self._provider = provider

    async def execute(
        self,
        segment: TranscriptSegment,
        *,
        target_level: GermanLevel,
    ) -> AssistUpdate:
        """Simplify the segment at the requested level or return a failed update."""

        try:
            result = await self._provider.simplify(
                GermanSimplificationRequest(
                    text=segment.text,
                    target_level=target_level,
                )
            )
        except ProviderError:
            return AssistUpdate(
                transcript_id=segment.transcript_id,
                capability=AssistCapability.SIMPLIFICATION,
                state=AssistState.FAILED,
            )
        return AssistUpdate(
            transcript_id=segment.transcript_id,
            capability=AssistCapability.SIMPLIFICATION,
            state=AssistState.READY,
            simplified_text=result.simplified_text,
        )
