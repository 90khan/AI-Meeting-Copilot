"""Tests for the bounded asynchronous captured-audio chunk buffer."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.application.dto import AudioSource, CapturedAudioChunk
from app.application.dto.ai import AudioFormat, AudioInput
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId
from app.infrastructure.audio import BoundedAudioChunkBuffer


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


def test_constructor_requires_a_positive_capacity() -> None:
    """The buffer cannot be created without positive storage capacity."""

    with pytest.raises(ApplicationValidationError, match="greater than zero"):
        BoundedAudioChunkBuffer(max_size=0)


def test_put_and_get_preserve_fifo_order_and_size() -> None:
    """Chunks below capacity are returned in insertion order."""

    async def exercise() -> None:
        buffer = BoundedAudioChunkBuffer(max_size=3)
        first = _chunk(0)
        second = _chunk(1)

        assert await buffer.put(first) is None
        assert await buffer.put(second) is None
        assert buffer.size == 2
        assert await buffer.get() is first
        assert await buffer.get() is second
        assert buffer.size == 0

    asyncio.run(exercise())


def test_full_buffer_drops_oldest_chunk_and_preserves_newest_chunks() -> None:
    """Overload drops only the oldest unprocessed chunk."""

    async def exercise() -> None:
        buffer = BoundedAudioChunkBuffer(max_size=2)
        first = _chunk(0)
        second = _chunk(1)
        third = _chunk(2)

        await buffer.put(first)
        await buffer.put(second)
        dropped = await buffer.put(third)

        assert dropped is first
        assert buffer.size == 2
        assert await buffer.get() is second
        assert await buffer.get() is third

    asyncio.run(exercise())


def test_waiting_consumer_wakes_when_a_chunk_is_added() -> None:
    """A waiting get call is notified without polling when data arrives."""

    async def exercise() -> None:
        buffer = BoundedAudioChunkBuffer()
        chunk = _chunk(0)
        consumer = asyncio.create_task(buffer.get())

        await asyncio.sleep(0)
        await buffer.put(chunk)

        assert await consumer is chunk

    asyncio.run(exercise())


def test_close_is_idempotent_and_drains_buffered_chunks() -> None:
    """Closure rejects new writes but keeps pre-existing chunks available to drain."""

    async def exercise() -> None:
        buffer = BoundedAudioChunkBuffer()
        chunk = _chunk(0)
        await buffer.put(chunk)

        await buffer.close()
        await buffer.close()

        assert buffer.is_closed
        assert await buffer.get() is chunk
        with pytest.raises(RuntimeError, match="closed and empty"):
            await buffer.get()
        with pytest.raises(RuntimeError, match="closed"):
            await buffer.put(_chunk(1))

    asyncio.run(exercise())


def test_multiple_producers_never_exceed_capacity() -> None:
    """Concurrent producers retain no more than the fixed number of newest chunks."""

    async def exercise() -> None:
        buffer = BoundedAudioChunkBuffer(max_size=3)
        chunks = [_chunk(sequence) for sequence in range(6)]

        await asyncio.gather(*(buffer.put(chunk) for chunk in chunks))

        assert buffer.size == 3
        drained = [await buffer.get() for _ in range(3)]
        assert [chunk.sequence for chunk in drained] == [3, 4, 5]

    asyncio.run(exercise())


def test_buffer_returns_the_original_immutable_chunk_instance() -> None:
    """The buffer does not replace or mutate a captured audio chunk."""

    async def exercise() -> None:
        buffer = BoundedAudioChunkBuffer()
        chunk = _chunk(7)

        await buffer.put(chunk)

        returned_chunk = await buffer.get()
        assert returned_chunk is chunk
        assert returned_chunk.audio.data == b"wav"

    asyncio.run(exercise())
