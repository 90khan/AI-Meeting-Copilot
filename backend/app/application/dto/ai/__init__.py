"""Shared AI application data transfer objects."""

from app.application.dto.ai.language import LanguageCode
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
from app.application.dto.ai.translation import TranslationRequest, TranslationResult

__all__ = [
    "AudioInput",
    "GermanLevel",
    "GermanSimplificationRequest",
    "GermanSimplificationResult",
    "LanguageCode",
    "SpeechToTextRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
    "TranslationRequest",
    "TranslationResult",
]
