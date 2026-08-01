"""AI capability contracts for the application layer."""

from app.application.interfaces.ai.german_simplification import (
    GermanSimplificationProvider,
)
from app.application.interfaces.ai.meeting_summarization import (
    MeetingSummarizationProvider,
)
from app.application.interfaces.ai.reply_coaching import ReplyCoachingProvider
from app.application.interfaces.ai.speech_to_text import SpeechToTextProvider
from app.application.interfaces.ai.translation import TranslationProvider

__all__ = [
    "GermanSimplificationProvider",
    "MeetingSummarizationProvider",
    "ReplyCoachingProvider",
    "SpeechToTextProvider",
    "TranslationProvider",
]
