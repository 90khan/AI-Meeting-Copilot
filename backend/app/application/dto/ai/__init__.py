"""Shared AI application data transfer objects."""

from app.application.dto.ai.language import LanguageCode
from app.application.dto.ai.speech import (
    AudioInput,
    SpeechToTextRequest,
    TranscriptionResult,
    TranscriptionSegment,
)

__all__ = [
    "AudioInput",
    "LanguageCode",
    "SpeechToTextRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
]
