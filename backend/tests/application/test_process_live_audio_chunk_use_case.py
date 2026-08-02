"""Tests for the live-audio chunk processing application use case."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.dto import (
    AddTranscriptCommand,
    AddTranscriptResult,
    AudioSource,
    CapturedAudioChunk,
    ProcessLiveAudioChunkCommand,
)
from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    LanguageCode,
    SpeechToTextRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.application.exceptions import (
    ApplicationValidationError,
    ProviderUnavailableError,
)
from app.application.services import TranscriptDeduplicator
from app.application.use_cases import (
    ProcessLiveAudioChunkUseCase,
)
from app.domain.exceptions import ValidationError
from app.domain.value_objects import MeetingId


class FakeSpeechToTextProvider:
    """Speech-to-text fake that records the request and returns one configured value."""

    def __init__(self, result: TranscriptionResult | Exception) -> None:
        """Initialize the fake with a result or propagated provider failure."""

        self._result = result
        self.requests: list[SpeechToTextRequest] = []

    async def transcribe(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Record the request and return the configured result."""

        self.requests.append(request)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeAddTranscriptUseCase:
    """Transcript-persistence boundary fake with call tracking."""

    def __init__(self, error: Exception | None = None) -> None:
        """Initialize optional failure behavior and command tracking."""

        self._error = error
        self.commands: list[AddTranscriptCommand] = []

    async def execute(self, command: AddTranscriptCommand) -> AddTranscriptResult:
        """Record one command or propagate the configured persistence error."""

        self.commands.append(command)
        if self._error is not None:
            raise self._error
        return AddTranscriptResult(transcript_id=uuid4())


def _chunk(*, sequence: int = 3) -> CapturedAudioChunk:
    return CapturedAudioChunk(
        meeting_id=MeetingId.new(),
        sequence=sequence,
        capture_started_at=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        audio=AudioInput(
            data=b"wav",
            sample_rate_hz=16_000,
            channels=1,
            audio_format=AudioFormat.WAV,
        ),
        source=AudioSource.MIXED,
    )


def _result(*segments: TranscriptionSegment) -> TranscriptionResult:
    return TranscriptionResult(
        language=LanguageCode(value="de"),
        segments=segments,
        duration_seconds=4.0,
    )


def _use_case(
    result: TranscriptionResult | Exception,
    add_transcript_use_case: FakeAddTranscriptUseCase,
) -> tuple[ProcessLiveAudioChunkUseCase, FakeSpeechToTextProvider]:
    provider = FakeSpeechToTextProvider(result)
    return (
        ProcessLiveAudioChunkUseCase(
            speech_to_text_provider=provider,
            transcript_deduplicator=TranscriptDeduplicator(),
            add_transcript_use_case=add_transcript_use_case,  # type: ignore[arg-type]
        ),
        provider,
    )


def test_execute_forwards_speech_request_and_persists_segments_in_order() -> None:
    """Accepted provider segments retain order, timestamps, and speaker attribution."""

    chunk = _chunk()
    add_transcript_use_case = FakeAddTranscriptUseCase()
    use_case, provider = _use_case(
        _result(
            TranscriptionSegment(
                text="Hello world",
                start_seconds=0.5,
                end_seconds=1.0,
            ),
            TranscriptionSegment(
                text="world next",
                start_seconds=1.5,
                end_seconds=2.0,
                speaker="Mira",
            ),
        ),
        add_transcript_use_case,
    )
    language_hint = LanguageCode(value="de-DE")

    result = asyncio.run(
        use_case.execute(
            ProcessLiveAudioChunkCommand(
                chunk=chunk,
                language_hint=language_hint,
            )
        )
    )

    assert provider.requests == [
        SpeechToTextRequest(audio=chunk.audio, language_hint=language_hint)
    ]
    assert [segment.text for segment in result.accepted_segments] == [
        "Hello world",
        "next",
    ]
    assert [segment.speaker for segment in result.accepted_segments] == [
        "Unknown",
        "Mira",
    ]
    assert [command.text for command in add_transcript_use_case.commands] == [
        "Hello world",
        "next",
    ]
    assert [command.timestamp for command in add_transcript_use_case.commands] == [
        datetime(2026, 8, 2, 10, 0, 0, 500_000, tzinfo=UTC),
        datetime(2026, 8, 2, 10, 0, 1, 500_000, tzinfo=UTC),
    ]
    assert result.skipped_silence is False


def test_execute_returns_skipped_silence_for_an_empty_provider_result() -> None:
    """A transcription with no segments performs no transcript persistence."""

    add_transcript_use_case = FakeAddTranscriptUseCase()
    use_case, _ = _use_case(_result(), add_transcript_use_case)

    result = asyncio.run(
        use_case.execute(ProcessLiveAudioChunkCommand(chunk=_chunk(sequence=4)))
    )

    assert result.chunk_sequence == 4
    assert result.accepted_segments == ()
    assert result.skipped_silence is True
    assert add_transcript_use_case.commands == []


def test_execute_removes_duplicate_prefix_and_skips_full_duplicate() -> None:
    """Only non-duplicated text is persisted from overlap-prone provider output."""

    chunk = _chunk()
    add_transcript_use_case = FakeAddTranscriptUseCase()
    use_case, _ = _use_case(
        _result(
            TranscriptionSegment(
                text="Hello there",
                start_seconds=0.0,
                end_seconds=0.5,
            ),
            TranscriptionSegment(
                text="there",
                start_seconds=0.5,
                end_seconds=1.0,
            ),
        ),
        add_transcript_use_case,
    )

    result = asyncio.run(
        use_case.execute(
            ProcessLiveAudioChunkCommand(
                chunk=chunk,
                previous_accepted_text="hello",
            )
        )
    )

    assert [segment.text for segment in result.accepted_segments] == ["there"]
    assert [command.text for command in add_transcript_use_case.commands] == ["there"]
    assert result.skipped_silence is False


def test_execute_reports_silence_when_every_segment_is_fully_duplicated() -> None:
    """Fully duplicated provider segments are not persisted."""

    add_transcript_use_case = FakeAddTranscriptUseCase()
    use_case, _ = _use_case(
        _result(
            TranscriptionSegment(
                text="HELLO",
                start_seconds=0.0,
                end_seconds=0.5,
            )
        ),
        add_transcript_use_case,
    )

    result = asyncio.run(
        use_case.execute(
            ProcessLiveAudioChunkCommand(
                chunk=_chunk(),
                previous_accepted_text="hello",
            )
        )
    )

    assert result.accepted_segments == ()
    assert result.skipped_silence is True
    assert add_transcript_use_case.commands == []


def test_execute_propagates_provider_errors_unchanged() -> None:
    """Provider failures are not caught or converted by application orchestration."""

    error = ProviderUnavailableError("Speech provider is unavailable.")
    add_transcript_use_case = FakeAddTranscriptUseCase()
    use_case, _ = _use_case(error, add_transcript_use_case)

    with pytest.raises(ProviderUnavailableError) as raised:
        asyncio.run(use_case.execute(ProcessLiveAudioChunkCommand(chunk=_chunk())))

    assert raised.value is error
    assert add_transcript_use_case.commands == []


def test_execute_propagates_persistence_errors_unchanged() -> None:
    """Persistence-boundary failures are not caught or converted."""

    error = ValidationError("Transcript rejected.")
    add_transcript_use_case = FakeAddTranscriptUseCase(error)
    use_case, _ = _use_case(
        _result(
            TranscriptionSegment(
                text="Hello",
                start_seconds=0.0,
                end_seconds=0.5,
            )
        ),
        add_transcript_use_case,
    )

    with pytest.raises(ValidationError) as raised:
        asyncio.run(use_case.execute(ProcessLiveAudioChunkCommand(chunk=_chunk())))

    assert raised.value is error
    assert len(add_transcript_use_case.commands) == 1


def test_command_rejects_blank_previous_accepted_text() -> None:
    """Bounded de-duplication context must contain transcript text when supplied."""

    with pytest.raises(ApplicationValidationError, match="Previous accepted text"):
        ProcessLiveAudioChunkCommand(
            chunk=_chunk(),
            previous_accepted_text=" ",
        )
