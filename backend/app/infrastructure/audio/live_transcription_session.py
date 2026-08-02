"""Buffered coordination for one active live-transcription session."""

import asyncio

from app.application.dto import (
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
    LiveTranscriptionStatus,
    LiveTranscriptionStatusKind,
    ProcessLiveAudioChunkCommand,
)
from app.application.use_cases import ProcessLiveAudioChunkUseCase
from app.infrastructure.audio.bounded_chunk_buffer import BoundedAudioChunkBuffer


class BufferedLiveTranscriptionSession:
    """Coordinate buffered sequential processing for one active session."""

    def __init__(
        self,
        *,
        buffer: BoundedAudioChunkBuffer,
        processor: ProcessLiveAudioChunkUseCase,
    ) -> None:
        """Initialize an active session with explicit processing boundaries."""

        self._buffer = buffer
        self._processor = processor
        self._previous_accepted_text: str | None = None
        self._is_stopping = False
        self._processing_lock = asyncio.Lock()

    async def process_chunk(
        self,
        chunk: CapturedAudioChunk,
    ) -> LiveTranscriptionChunkResult:
        """Buffer and sequentially process one finalized captured-audio chunk."""

        async with self._processing_lock:
            if self._is_stopping:
                raise RuntimeError("Live transcription session is stopping.")

            dropped_chunk = await self._buffer.put(chunk)
            buffered_chunk = await self._buffer.get()
            result = await self._processor.execute(
                ProcessLiveAudioChunkCommand(
                    chunk=buffered_chunk,
                    previous_accepted_text=self._previous_accepted_text,
                )
            )
            if result.accepted_segments:
                self._previous_accepted_text = result.accepted_segments[-1].text

            if dropped_chunk is None:
                return result

            return LiveTranscriptionChunkResult(
                chunk_sequence=result.chunk_sequence,
                accepted_segments=result.accepted_segments,
                skipped_silence=result.skipped_silence,
                status=LiveTranscriptionStatus(
                    kind=LiveTranscriptionStatusKind.GAP,
                    message=(
                        "An audio chunk was dropped because transcription fell behind."
                    ),
                    chunk_sequence=chunk.sequence,
                ),
            )

    async def stop(self) -> None:
        """Stop accepting chunks, close the buffer, and clear session-local context."""

        if self._is_stopping:
            async with self._processing_lock:
                return

        self._is_stopping = True
        async with self._processing_lock:
            await self._buffer.close()
            self._previous_accepted_text = None
