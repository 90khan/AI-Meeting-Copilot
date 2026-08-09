"""Tests for authenticated internal binary recording playback transport."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import UUID

from app.api.routes.recording_playback import (
    PLAYBACK_FRAME_AUDIO_SEGMENT,
    PLAYBACK_FRAME_END,
    PLAYBACK_FRAME_ERROR,
    PLAYBACK_FRAME_HEADER,
    PLAYBACK_FRAME_VERSION,
    router,
)
from app.application.dto.recordings import (
    RecordingMediaFormat,
    RecordingPlaybackInfo,
    RecordingPlaybackSegment,
)
from app.application.exceptions import RecordingPlaybackUnavailableError
from app.domain.value_objects import MeetingId
from fastapi import FastAPI
from fastapi.testclient import TestClient

_MEETING_ID = UUID(int=1)


class _Validator:
    def validate(self, token: str) -> None:
        if token != "token":
            from app.application.exceptions import ProviderAuthenticationError

            raise ProviderAuthenticationError("private")


class _Reader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.read_count = 0

    async def get_info(self, meeting_id: MeetingId) -> RecordingPlaybackInfo:
        if self.fail:
            raise RecordingPlaybackUnavailableError()
        return RecordingPlaybackInfo(
            recording_id=UUID(int=2),
            meeting_id=meeting_id,
            format=RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1,
            duration_seconds=2.0,
            segment_count=2,
            has_gaps=True,
        )

    async def read_segments(
        self, meeting_id: MeetingId
    ) -> AsyncIterator[RecordingPlaybackSegment]:
        self.read_count += 1
        yield RecordingPlaybackSegment(segment_index=2, plaintext_audio=b"two")
        if self.fail:
            raise RecordingPlaybackUnavailableError("private path")
        yield RecordingPlaybackSegment(segment_index=5, plaintext_audio=b"five")


def _client(reader: _Reader) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(
        get_sidecar_token_validator=lambda: _Validator(),
        get_recording_playback_reader=lambda: reader,
    )
    return TestClient(app)


def _frames(payload: bytes) -> list[tuple[int, int, bytes]]:
    frames = []
    cursor = 0
    while cursor < len(payload):
        version, kind, index, length = PLAYBACK_FRAME_HEADER.unpack_from(
            payload, cursor
        )
        cursor += PLAYBACK_FRAME_HEADER.size
        body = payload[cursor : cursor + length]
        cursor += length
        assert version == PLAYBACK_FRAME_VERSION
        assert len(body) == length
        frames.append((kind, index, body))
    return frames


def test_info_and_stream_are_authenticated_and_ordered() -> None:
    client = _client(_Reader())
    headers = {"x-ai-meeting-copilot-token": "token"}
    info_url = f"/api/v1/internal/recordings/{_MEETING_ID}/playback-info"
    stream_url = f"/api/v1/internal/recordings/{_MEETING_ID}/playback-stream"
    info = client.get(info_url, headers=headers)
    stream = client.get(stream_url, headers=headers)
    assert info.status_code == stream.status_code == 200
    assert set(info.json()) == {
        "meeting_id",
        "format",
        "duration_seconds",
        "segment_count",
        "has_gaps",
    }
    assert _frames(stream.content) == [
        (PLAYBACK_FRAME_AUDIO_SEGMENT, 2, b"two"),
        (PLAYBACK_FRAME_AUDIO_SEGMENT, 5, b"five"),
        (PLAYBACK_FRAME_END, 0, b""),
    ]


def test_invalid_token_and_unavailable_or_failed_playback_are_safe() -> None:
    client = _client(_Reader(fail=True))
    info_url = f"/api/v1/internal/recordings/{_MEETING_ID}/playback-info"
    stream_url = f"/api/v1/internal/recordings/{_MEETING_ID}/playback-stream"
    missing = client.get(info_url)
    invalid = client.get(stream_url, headers={"x-ai-meeting-copilot-token": "bad"})
    headers = {"x-ai-meeting-copilot-token": "token"}
    info = client.get(info_url, headers=headers)
    stream = client.get(stream_url, headers=headers)
    assert missing.status_code == invalid.status_code == 401
    assert info.status_code == 404
    assert "private" not in stream.text
    assert _frames(stream.content) == [
        (PLAYBACK_FRAME_AUDIO_SEGMENT, 2, b"two"),
        (PLAYBACK_FRAME_ERROR, 0, b""),
    ]
