"""Use case for processing one finalized live-audio chunk."""

import time
from datetime import timedelta

from app.application.dto import (
    AddTranscriptCommand,
    LiveTranscriptionChunkResult,
    ProcessedTranscriptSegment,
    ProcessLiveAudioChunkCommand,
)
from app.application.dto.ai import SpeechToTextRequest
from app.application.interfaces import SpeechToTextProvider
from app.application.services import TranscriptDeduplicator
from app.application.use_cases.add_transcript import AddTranscriptUseCase
from app.core.logging import get_logger
from app.core.throughput_diagnostics import (
    emit_throughput,
    estimate_processed_audio_ms,
    estimate_v1_pcm16_wav_duration_ms,
    throughput_chunk_sequence,
)

_LOGGER = get_logger(__name__)


class ProcessLiveAudioChunkUseCase:
    """Transcribe, de-duplicate, and persist one finalized audio chunk."""

    def __init__(
        self,
        *,
        speech_to_text_provider: SpeechToTextProvider,
        transcript_deduplicator: TranscriptDeduplicator,
        add_transcript_use_case: AddTranscriptUseCase,
    ) -> None:
        """Initialize the use case with its explicit application dependencies."""

        self._speech_to_text_provider = speech_to_text_provider
        self._transcript_deduplicator = transcript_deduplicator
        self._add_transcript_use_case = add_transcript_use_case

    async def execute(
        self,
        command: ProcessLiveAudioChunkCommand,
    ) -> LiveTranscriptionChunkResult:
        """Process one chunk in provider order and persist accepted segments."""

        sequence = command.chunk.sequence
        audio_duration_ms = estimate_v1_pcm16_wav_duration_ms(
            byte_length=len(command.chunk.audio.data),
            sample_rate_hz=command.chunk.audio.sample_rate_hz,
            channels=command.chunk.audio.channels,
        )
        processed_audio_ms = estimate_processed_audio_ms(
            audio_duration_ms=audio_duration_ms,
            overlap_seconds=command.chunk.overlap_seconds,
            is_first_chunk=sequence == 0,
        )
        stt_started_at = time.monotonic()
        emit_throughput(
            "stt_started",
            sequence=sequence,
            audio_duration_ms=audio_duration_ms,
            processed_audio_ms=processed_audio_ms,
        )
        _LOGGER.debug("live-transcription backend stt started sequence=%d", sequence)
        with throughput_chunk_sequence(sequence):
            transcription = await self._speech_to_text_provider.transcribe(
                SpeechToTextRequest(
                    audio=command.chunk.audio,
                    language_hint=command.language_hint,
                )
            )
        _LOGGER.debug(
            "live-transcription backend stt completed sequence=%d elapsed_ms=%d",
            sequence,
            _elapsed_milliseconds(stt_started_at),
        )
        emit_throughput(
            "stt_completed",
            sequence=sequence,
            elapsed_ms=_elapsed_milliseconds(stt_started_at),
            stt_duration_ms=_elapsed_milliseconds(stt_started_at),
            audio_duration_ms=audio_duration_ms,
            processed_audio_ms=processed_audio_ms,
        )
        postprocess_started_at = time.monotonic()
        if not transcription.segments:
            _LOGGER.debug(
                "live-transcription backend postprocess completed sequence=%d "
                "elapsed_ms=%d",
                sequence,
                _elapsed_milliseconds(postprocess_started_at),
            )
            emit_throughput(
                "postprocess_completed",
                sequence=sequence,
                elapsed_ms=_elapsed_milliseconds(postprocess_started_at),
                post_processing_ms=_elapsed_milliseconds(postprocess_started_at),
            )
            return LiveTranscriptionChunkResult(
                chunk_sequence=sequence,
                accepted_segments=(),
                skipped_silence=True,
            )

        previous_text = command.previous_accepted_text
        accepted_segments: list[ProcessedTranscriptSegment] = []
        for transcription_segment in transcription.segments:
            accepted_text = self._transcript_deduplicator.deduplicate(
                previous_text=previous_text,
                current_text=transcription_segment.text,
            )
            if accepted_text is None:
                continue

            timestamp = command.chunk.capture_started_at + timedelta(
                seconds=transcription_segment.start_seconds
            )
            speaker = transcription_segment.speaker or "Unknown"
            persisted_transcript = await self._add_transcript_use_case.execute(
                AddTranscriptCommand(
                    meeting_id=command.chunk.meeting_id,
                    speaker=speaker,
                    text=accepted_text,
                    timestamp=timestamp,
                )
            )
            processed_segment = ProcessedTranscriptSegment(
                transcript_id=persisted_transcript.transcript_id,
                text=accepted_text,
                timestamp=timestamp,
                source=command.chunk.source,
                speaker=speaker,
            )
            accepted_segments.append(processed_segment)
            previous_text = accepted_text

        _LOGGER.debug(
            "live-transcription backend postprocess completed sequence=%d "
            "elapsed_ms=%d",
            sequence,
            _elapsed_milliseconds(postprocess_started_at),
        )
        emit_throughput(
            "postprocess_completed",
            sequence=sequence,
            elapsed_ms=_elapsed_milliseconds(postprocess_started_at),
            post_processing_ms=_elapsed_milliseconds(postprocess_started_at),
        )
        return LiveTranscriptionChunkResult(
            chunk_sequence=sequence,
            accepted_segments=tuple(accepted_segments),
            skipped_silence=not accepted_segments,
        )


def _elapsed_milliseconds(started_at: float) -> int:
    """Return a privacy-safe monotonic elapsed duration for debug diagnostics."""

    return int((time.monotonic() - started_at) * 1_000)
