"""Tests for the live-transcription application contract."""

import inspect

from app.application.dto import (
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
)
from app.application.interfaces import LiveTranscriptionSession


class FakeLiveTranscriptionSession:
    """Minimal structural implementation of the live-transcription contract."""

    async def process_chunk(
        self,
        chunk: CapturedAudioChunk,
    ) -> LiveTranscriptionChunkResult:
        """Return an empty successful result for the supplied chunk."""

        return LiveTranscriptionChunkResult(
            chunk_sequence=chunk.sequence,
            accepted_segments=(),
            skipped_silence=True,
        )

    async def stop(self) -> None:
        """Stop the fake session."""


def test_live_transcription_session_protocol_is_async_and_structural() -> None:
    """The protocol accepts asynchronous structural implementations."""

    session = FakeLiveTranscriptionSession()

    assert inspect.iscoroutinefunction(LiveTranscriptionSession.process_chunk)
    assert inspect.iscoroutinefunction(LiveTranscriptionSession.stop)
    assert isinstance(session, LiveTranscriptionSession)
