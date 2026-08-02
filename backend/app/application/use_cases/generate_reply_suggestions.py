"""Transient reply coaching from bounded finalized transcript context."""

from app.application.dto.ai import LanguageCode, ReplyCoachingRequest, ReplyTone
from app.application.dto.assist_mode import (
    AssistCapability,
    AssistState,
    AssistUpdate,
    ReplyContext,
)
from app.application.exceptions import ProviderError
from app.application.interfaces import ReplyCoachingProvider


class GenerateReplySuggestionsUseCase:
    """Generate up to two transient German reply suggestions for latest context."""

    def __init__(self, *, provider: ReplyCoachingProvider) -> None:
        """Initialize the explicit reply-coaching capability dependency."""

        self._provider = provider

    async def execute(self, context: ReplyContext) -> AssistUpdate:
        """Coach replies without persistence or non-provider error handling."""

        try:
            result = await self._provider.suggest_replies(
                ReplyCoachingRequest(
                    conversation_context=context.conversation_text,
                    latest_utterance=context.newest_finalized_segment.text,
                    response_language=LanguageCode(value="de"),
                    tone=ReplyTone.PROFESSIONAL,
                    max_suggestions=2,
                )
            )
        except ProviderError:
            return AssistUpdate(
                transcript_id=context.newest_finalized_segment.transcript_id,
                capability=AssistCapability.REPLY_COACHING,
                state=AssistState.FAILED,
            )
        return AssistUpdate(
            transcript_id=context.newest_finalized_segment.transcript_id,
            capability=AssistCapability.REPLY_COACHING,
            state=AssistState.READY,
            reply_suggestions=result.suggestions[:2],
        )
