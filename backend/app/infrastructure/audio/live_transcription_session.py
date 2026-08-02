"""Buffered coordination for one active live-transcription session."""

import asyncio

from app.application.dto import (
    AudioSource,
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
    LiveTranscriptionStatus,
    LiveTranscriptionStatusKind,
    ProcessLiveAudioChunkCommand,
)
from app.application.dto.ai import LanguageCode
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import ProcessLiveAudioChunkUseCase
from app.domain.value_objects import MeetingId
from app.infrastructure.audio.bounded_chunk_buffer import BoundedAudioChunkBuffer


class BufferedLiveTranscriptionSession:
    """Coordinate buffered sequential processing for one active session."""

    def __init__(
        self,
        *,
        meeting_id: MeetingId,
        language_hint: LanguageCode | None,
        source: AudioSource,
        buffer: BoundedAudioChunkBuffer,
        processor: ProcessLiveAudioChunkUseCase,
    ) -> None:
        """Initialize an active session with explicit processing boundaries."""

        self._meeting_id = meeting_id
        self._language_hint = language_hint
        self._source = source
        self._buffer = buffer
        self._processor = processor
        self._previous_accepted_text: str | None = None
        self._is_stopping = False
        self._processing_lock = asyncio.Lock()

    @property
    def meeting_id(self) -> MeetingId:
        """Return the Meeting identity bound to this session."""

        return self._meeting_id

    @property
    def language_hint(self) -> LanguageCode | None:
        """Return the optional language hint fixed for this session."""

        return self._language_hint

    @property
    def source(self) -> AudioSource:
        """Return the audio source fixed for this session."""

        return self._source

    async def process_chunk(
        self,
        chunk: CapturedAudioChunk,
    ) -> LiveTranscriptionChunkResult:
        """Buffer and sequentially process one finalized captured-audio chunk."""

        async with self._processing_lock:
            if self._is_stopping:
                raise RuntimeError("Live transcription session is stopping.")
            if chunk.meeting_id != self._meeting_id:
                raise ApplicationValidationError(
                    "Audio chunk Meeting ID does not match the session."
                )
            if chunk.source is not self._source:
                raise ApplicationValidationError(
                    "Audio chunk source does not match the session."
                )

            dropped_chunk = await self._buffer.put(chunk)
            buffered_chunk = await self._buffer.get()
            result = await self._processor.execute(
                ProcessLiveAudioChunkCommand(
                    chunk=buffered_chunk,
                    language_hint=self._language_hint,
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
