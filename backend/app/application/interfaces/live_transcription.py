"""Application contract for processing finalized live-audio chunks."""

from typing import Protocol, runtime_checkable

from app.application.dto.live_transcription import (
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
)


@runtime_checkable
class LiveTranscriptionSession(Protocol):
    """Process finalized live-audio chunks for one active Meeting session."""

    async def process_chunk(
        self,
        chunk: CapturedAudioChunk,
    ) -> LiveTranscriptionChunkResult:
        """Process one finalized audio chunk."""

    async def stop(self) -> None:
        """Stop processing the active live-transcription session."""
