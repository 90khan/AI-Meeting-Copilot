"""Token-authenticated loopback stream for trusted desktop recording playback."""

import asyncio
import struct
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.application.exceptions import (
    ProviderAuthenticationError,
    RecordingPlaybackUnavailableError,
)
from app.core.container import Container
from app.domain.value_objects import MeetingId

router = APIRouter(prefix="/api/v1/internal/recordings")
# Versioned binary playback framing. Every frame is ``version, kind, index,
# payload_length`` in big-endian order, followed by exactly payload_length bytes.
# END and ERROR use index=0 and an empty payload. ERROR is intentionally generic.
PLAYBACK_FRAME_VERSION = 1
PLAYBACK_FRAME_AUDIO_SEGMENT = 1
PLAYBACK_FRAME_END = 2
PLAYBACK_FRAME_ERROR = 3
PLAYBACK_FRAME_HEADER = struct.Struct(">BBII")
MAX_PLAYBACK_FRAME_PAYLOAD_BYTES = 67_108_864


class _PlaybackInfoResponse(BaseModel):
    meeting_id: UUID
    format: str
    capture_anchor_utc: str
    duration_seconds: float | None
    segment_count: int
    has_gaps: bool


@router.get("/{meeting_id}/playback-info", response_model=_PlaybackInfoResponse)
async def playback_info(
    meeting_id: UUID, request: Request, x_ai_meeting_copilot_token: str = Header("")
) -> _PlaybackInfoResponse:
    container = _authenticate(request, x_ai_meeting_copilot_token)
    try:
        info = await container.get_recording_playback_reader().get_info(
            MeetingId(meeting_id)
        )
    except RecordingPlaybackUnavailableError as error:
        raise HTTPException(404, "Playback is unavailable.") from error
    return _PlaybackInfoResponse(
        meeting_id=info.meeting_id.value,
        format=info.format,
        capture_anchor_utc=info.capture_anchor_utc.isoformat().replace("+00:00", "Z"),
        duration_seconds=info.duration_seconds,
        segment_count=info.segment_count,
        has_gaps=info.has_gaps,
    )


@router.get("/{meeting_id}/playback-stream")
async def playback_stream(
    meeting_id: UUID, request: Request, x_ai_meeting_copilot_token: str = Header("")
) -> StreamingResponse:
    container = _authenticate(request, x_ai_meeting_copilot_token)
    reader = container.get_recording_playback_reader()

    async def frames() -> AsyncIterator[bytes]:
        try:
            async for segment in reader.read_segments(MeetingId(meeting_id)):
                if await request.is_disconnected():
                    return
                payload_length = len(segment.plaintext_audio)
                if payload_length > MAX_PLAYBACK_FRAME_PAYLOAD_BYTES:
                    yield _terminal_frame(PLAYBACK_FRAME_ERROR)
                    return
                yield PLAYBACK_FRAME_HEADER.pack(
                    PLAYBACK_FRAME_VERSION,
                    PLAYBACK_FRAME_AUDIO_SEGMENT,
                    segment.segment_index,
                    payload_length,
                )
                yield segment.plaintext_audio
            if not await request.is_disconnected():
                yield _terminal_frame(PLAYBACK_FRAME_END)
        except asyncio.CancelledError:
            raise
        except RecordingPlaybackUnavailableError:
            if not await request.is_disconnected():
                yield _terminal_frame(PLAYBACK_FRAME_ERROR)
            return

    return StreamingResponse(frames(), media_type="application/octet-stream")


def _authenticate(request: Request, token: str) -> Container:
    container = cast(Container, request.app.state.container)
    try:
        container.get_sidecar_token_validator().validate(token)
    except (ProviderAuthenticationError, RuntimeError) as error:
        raise HTTPException(401, "Playback is unavailable.") from error
    return container


def _terminal_frame(kind: int) -> bytes:
    """Build a zero-payload terminal frame without error details."""

    return PLAYBACK_FRAME_HEADER.pack(PLAYBACK_FRAME_VERSION, kind, 0, 0)
