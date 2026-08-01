"""Faster-Whisper infrastructure components."""

from app.infrastructure.providers.faster_whisper.model_manager import (
    FasterWhisperModelManager,
)
from app.infrastructure.providers.faster_whisper.speech_to_text import (
    FasterWhisperSpeechToTextProvider,
)

__all__ = ["FasterWhisperModelManager", "FasterWhisperSpeechToTextProvider"]
