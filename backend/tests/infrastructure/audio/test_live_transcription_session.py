"""Tests for buffered live-transcription session coordination."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.application.dto import (
    AudioSource,
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
    LiveTranscriptionStatus,
    LiveTranscriptionStatusKind,
    ProcessedTranscriptSegment,
)
from app.application.dto.ai import AudioFormat, AudioInput
from app.domain.value_objects import MeetingId
from app.infrastructure.audio import (
    BoundedAudioChunkBuffer,
    BufferedLiveTranscriptionSession,
)


class FakeProcessor:
    """Live-audio processor fake with explicit outcomes and command tracking."""

    def __init__(
        self,
        outcomes: list[LiveTranscriptionChunkResult | Exception],
    ) -> None:
        """Initialize the processor fake with sequential execution outcomes."""

        self._outcomes = outcomes
        self.commands: list[object] = []

    async def execute(self, command: object) -> LiveTranscriptionChunkResult:
        """Record the command and return or raise the next configured outcome."""

        self.commands.append(command)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _chunk(sequence: int) -> CapturedAudioChunk:
    return CapturedAudioChunk(
        meeting_id=MeetingId.new(),
        sequence=sequence,
        capture_started_at=datetime.now(UTC),
        audio=AudioInput(
            data=b"wav",
            sample_rate_hz=16_000,
            channels=1,
            audio_format=AudioFormat.WAV,
        ),
        source=AudioSource.MIXED,
    )


def _segment(text: str) -> ProcessedTranscriptSegment:
    return ProcessedTranscriptSegment(
        text=text,
        timestamp=datetime.now(UTC),
        source=AudioSource.MIXED,
    )


def _result(
    sequence: int,
    *segments: ProcessedTranscriptSegment,
    status: LiveTranscriptionStatus | None = None,
) -> LiveTranscriptionChunkResult:
    return LiveTranscriptionChunkResult(
        chunk_sequence=sequence,
        accepted_segments=segments,
        skipped_silence=not segments,
        status=status,
    )


def _session(
    outcomes: list[LiveTranscriptionChunkResult | Exception],
    *,
    max_size: int = 3,
) -> tuple[BufferedLiveTranscriptionSession, BoundedAudioChunkBuffer, FakeProcessor]:
    buffer = BoundedAudioChunkBuffer(max_size=max_size)
    processor = FakeProcessor(outcomes)
    return (
        BufferedLiveTranscriptionSession(
            buffer=buffer,
            processor=processor,  # type: ignore[arg-type]
        ),
        buffer,
        processor,
    )


def test_active_session_processes_one_chunk_and_updates_previous_text() -> None:
    """An active session forwards and retains only its final accepted segment text."""

    chunk = _chunk(0)
    result = _result(0, _segment("First"), _segment("Second"))
    session, _, processor = _session([result])

    returned_result = asyncio.run(session.process_chunk(chunk))

    command = processor.commands[0]
    assert returned_result is result
    assert command.chunk is chunk  # type: ignore[attr-defined]
    assert command.previous_accepted_text is None  # type: ignore[attr-defined]
    assert session._previous_accepted_text == "Second"


def test_session_preserves_fifo_order_for_prebuffered_chunks() -> None:
    """Each call processes the oldest buffered chunk before newer additions."""

    async def exercise() -> list[object]:
        first = _chunk(0)
        second = _chunk(1)
        third = _chunk(2)
        session, buffer, processor = _session([_result(0), _result(1)])
        await buffer.put(first)

        await session.process_chunk(second)
        await session.process_chunk(third)

        return processor.commands

    commands = asyncio.run(exercise())

    assert [command.chunk.sequence for command in commands] == [0, 1]  # type: ignore[attr-defined]


def test_silence_keeps_previous_text_unchanged() -> None:
    """A no-segment result does not discard useful bounded overlap context."""

    async def exercise() -> FakeProcessor:
        session, _, processor = _session(
            [_result(0, _segment("Retained")), _result(1), _result(2)]
        )

        await session.process_chunk(_chunk(0))
        await session.process_chunk(_chunk(1))
        await session.process_chunk(_chunk(2))
        return processor

    processor = asyncio.run(exercise())

    assert processor.commands[1].previous_accepted_text == "Retained"  # type: ignore[attr-defined]
    assert processor.commands[2].previous_accepted_text == "Retained"  # type: ignore[attr-defined]


def test_overload_decorates_the_current_result_with_a_gap_status() -> None:
    """Dropping the oldest buffered chunk produces a content-free gap status."""

    async def exercise() -> LiveTranscriptionChunkResult:
        session, buffer, _ = _session([_result(1, _segment("Accepted"))], max_size=1)
        await buffer.put(_chunk(0))
        return await session.process_chunk(_chunk(1))

    result = asyncio.run(exercise())

    assert result.status == LiveTranscriptionStatus(
        kind=LiveTranscriptionStatusKind.GAP,
        message="An audio chunk was dropped because transcription fell behind.",
        chunk_sequence=1,
    )
    assert [segment.text for segment in result.accepted_segments] == ["Accepted"]


def test_processor_status_is_preserved_when_no_chunk_is_dropped() -> None:
    """Normal processing returns the processor's original result unmodified."""

    status = LiveTranscriptionStatus(kind=LiveTranscriptionStatusKind.PROCESSING)
    processor_result = _result(0, status=status)
    session, _, _ = _session([processor_result])

    result = asyncio.run(session.process_chunk(_chunk(0)))

    assert result is processor_result
    assert result.status is status


def test_failed_processing_does_not_update_previous_text() -> None:
    """A processing failure leaves the last successfully accepted text intact."""

    async def exercise() -> FakeProcessor:
        failure = ValueError("processor failure")
        session, _, processor = _session(
            [_result(0, _segment("Retained")), failure, _result(2)]
        )
        await session.process_chunk(_chunk(0))
        with pytest.raises(ValueError, match="processor failure"):
            await session.process_chunk(_chunk(1))
        await session.process_chunk(_chunk(2))
        return processor

    processor = asyncio.run(exercise())

    assert processor.commands[2].previous_accepted_text == "Retained"  # type: ignore[attr-defined]


def test_stop_is_idempotent_and_rejects_further_processing() -> None:
    """Stopping closes the buffer and permanently rejects new public chunks."""

    async def exercise() -> None:
        session, buffer, _ = _session([])

        await session.stop()
        await session.stop()

        assert buffer.is_closed
        assert session._previous_accepted_text is None
        with pytest.raises(RuntimeError, match="stopping"):
            await session.process_chunk(_chunk(0))

    asyncio.run(exercise())
