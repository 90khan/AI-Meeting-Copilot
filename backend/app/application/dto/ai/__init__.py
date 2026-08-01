"""Shared AI application data transfer objects."""

from app.application.dto.ai.language import LanguageCode
from app.application.dto.ai.reply_coaching import (
    ReplyCoachingRequest,
    ReplyCoachingResult,
    ReplySuggestion,
    ReplyTone,
)
from app.application.dto.ai.simplification import (
    GermanLevel,
    GermanSimplificationRequest,
    GermanSimplificationResult,
)
from app.application.dto.ai.speech import (
    AudioInput,
    SpeechToTextRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.application.dto.ai.summarization import (
    ActionItemDraft,
    MeetingSummaryRequest,
    MeetingSummaryResult,
)
from app.application.dto.ai.translation import TranslationRequest, TranslationResult

__all__ = [
    "ActionItemDraft",
    "AudioInput",
    "GermanLevel",
    "GermanSimplificationRequest",
    "GermanSimplificationResult",
    "LanguageCode",
    "MeetingSummaryRequest",
    "MeetingSummaryResult",
    "ReplyCoachingRequest",
    "ReplyCoachingResult",
    "ReplySuggestion",
    "ReplyTone",
    "SpeechToTextRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
    "TranslationRequest",
    "TranslationResult",
]
