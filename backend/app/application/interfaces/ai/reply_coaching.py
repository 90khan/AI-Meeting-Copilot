"""Reply-coaching provider contract."""

from typing import Protocol, runtime_checkable

from app.application.dto.ai.reply_coaching import (
    ReplyCoachingRequest,
    ReplyCoachingResult,
)


@runtime_checkable
class ReplyCoachingProvider(Protocol):
    """Provides asynchronous reply coaching."""

    async def suggest_replies(
        self, request: ReplyCoachingRequest
    ) -> ReplyCoachingResult:
        """Suggest a bounded set of replies for the supplied conversation context."""
