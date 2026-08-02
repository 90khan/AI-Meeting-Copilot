"""Use case for processing one finalized live-audio chunk."""

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

        transcription = await self._speech_to_text_provider.transcribe(
            SpeechToTextRequest(
                audio=command.chunk.audio,
                language_hint=command.language_hint,
            )
        )
        if not transcription.segments:
            return LiveTranscriptionChunkResult(
                chunk_sequence=command.chunk.sequence,
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

        return LiveTranscriptionChunkResult(
            chunk_sequence=command.chunk.sequence,
            accepted_segments=tuple(accepted_segments),
            skipped_silence=not accepted_segments,
        )
