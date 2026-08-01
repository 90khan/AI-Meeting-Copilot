"""Speech-to-text provider contract."""

from typing import Protocol, runtime_checkable

from app.application.dto.ai.speech import SpeechToTextRequest, TranscriptionResult


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Provides asynchronous speech-to-text transcription."""

    async def transcribe(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Transcribe a complete audio request into timed speech segments."""
