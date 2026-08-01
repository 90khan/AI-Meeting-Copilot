"""Factory contracts for AI capability providers."""

from typing import Protocol, runtime_checkable

from app.application.interfaces.ai.german_simplification import (
    GermanSimplificationProvider,
)
from app.application.interfaces.ai.meeting_summarization import (
    MeetingSummarizationProvider,
)
from app.application.interfaces.ai.reply_coaching import ReplyCoachingProvider
from app.application.interfaces.ai.speech_to_text import SpeechToTextProvider
from app.application.interfaces.ai.translation import TranslationProvider


@runtime_checkable
class SpeechToTextProviderFactory(Protocol):
    """Creates a speech-to-text provider."""

    def __call__(self) -> SpeechToTextProvider:
        """Create a speech-to-text provider instance."""


@runtime_checkable
class TranslationProviderFactory(Protocol):
    """Creates a translation provider."""

    def __call__(self) -> TranslationProvider:
        """Create a translation provider instance."""


@runtime_checkable
class GermanSimplificationProviderFactory(Protocol):
    """Creates a German simplification provider."""

    def __call__(self) -> GermanSimplificationProvider:
        """Create a German simplification provider instance."""


@runtime_checkable
class ReplyCoachingProviderFactory(Protocol):
    """Creates a reply-coaching provider."""

    def __call__(self) -> ReplyCoachingProvider:
        """Create a reply-coaching provider instance."""


@runtime_checkable
class MeetingSummarizationProviderFactory(Protocol):
    """Creates a meeting-summarization provider."""

    def __call__(self) -> MeetingSummarizationProvider:
        """Create a meeting-summarization provider instance."""
