"""Loopback-only WebSocket endpoint for finalized live-audio chunks."""

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.live_transcription.assist_messages import WebSocketAssistUpdateSink
from app.api.live_transcription.binary_frames import parse_audio_chunk_frame
from app.api.live_transcription.protocol import (
    DEFAULT_MAX_BINARY_PAYLOAD_BYTES,
    DEFAULT_MAX_IN_FLIGHT_CHUNKS,
    PROTOCOL_VERSION,
    EndSessionMessage,
    HelloAckMessage,
    HelloMessage,
    ProtocolErrorMessage,
    SessionStartedMessage,
    SessionStartFailureStage,
    SessionStoppedMessage,
    StartSessionMessage,
    parse_protocol_message,
    serialize_protocol_message,
)
from app.application.dto import (
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
    LiveTranscriptionStatus,
    LiveTranscriptionStatusKind,
)
from app.application.dto.ai import AudioInput
from app.application.dto.assist_mode import TranscriptSegment
from app.application.dto.start_live_transcription_session_command import (
    StartLiveTranscriptionSessionCommand,
)
from app.application.exceptions import (
    ApplicationValidationError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderUnavailableError,
)
from app.application.interfaces import LiveTranscriptionSession
from app.application.services import (
    AssistModeConfiguration,
    AssistModeOrchestrator,
)
from app.core.container import Container
from app.core.logging import get_logger
from app.core.throughput_diagnostics import (
    emit_throughput,
    estimate_v1_pcm16_wav_duration_ms,
)
from app.domain.exceptions import InvalidStateTransitionError

router = APIRouter()

_HANDSHAKE_TIMEOUT_SECONDS = 5
_MAX_SEQUENCE_VIOLATIONS = 3
_PACKAGED_TAURI_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost"})
_DEBUG_VITE_ORIGINS = frozenset({"http://localhost:1420", "http://127.0.0.1:1420"})
_LOGGER = get_logger(__name__)


@router.websocket("/api/v1/live-transcription")
async def live_transcription(websocket: WebSocket) -> None:
    """Run one authenticated, sequential V1 live-transcription session.

    A failed Meeting validation is fatal for this connection and closes with
    4400. Empty sessions use ``last_sequence=0`` because the V1 DTO forbids a
    negative sequence sentinel.
    """

    if not _is_loopback_client(websocket):
        await websocket.close(code=4403)
        return
    if not _is_allowed_origin(websocket):
        await websocket.close(code=4403)
        return

    await websocket.accept()
    send_lock = asyncio.Lock()
    websocket.state.live_transcription_send_lock = send_lock
    active_session: LiveTranscriptionSession | None = None
    assist_orchestrator: AssistModeOrchestrator | None = None
    session_id: UUID | None = None
    expected_sequence = 0
    sequence_violations = 0
    is_closed = False

    try:
        container = _get_container(websocket)
        try:
            hello = await _receive_hello(websocket)
            container.get_sidecar_token_validator().validate(hello.token)
        except (ApplicationValidationError, ProviderAuthenticationError, RuntimeError):
            await websocket.close(code=4401)
            return

        await _send_control(
            websocket,
            HelloAckMessage(
                version=PROTOCOL_VERSION,
                connection_id=uuid4(),
                max_binary_payload_bytes=DEFAULT_MAX_BINARY_PAYLOAD_BYTES,
                max_in_flight_chunks=DEFAULT_MAX_IN_FLIGHT_CHUNKS,
            ),
        )

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

            text = message.get("text")
            if text is not None:
                control = await _parse_control_or_send_error(websocket, text)
                if control is None:
                    continue

                if isinstance(control, StartSessionMessage):
                    if active_session is not None:
                        await _send_protocol_error(
                            websocket,
                            code="session_already_started",
                            message="A live transcription session is already active.",
                            fatal=False,
                            session_id=session_id,
                        )
                        continue

                    try:
                        use_case = (
                            container.get_start_live_transcription_session_use_case()
                        )
                        validated = await use_case.execute(
                            StartLiveTranscriptionSessionCommand(
                                meeting_id=control.meeting_id,
                                language_hint=control.language_hint,
                                source=control.source,
                            )
                        )
                    except (LookupError, InvalidStateTransitionError):
                        await _send_protocol_error(
                            websocket,
                            code="session_start_rejected",
                            message="The Meeting cannot start live transcription.",
                            fatal=True,
                            request_id=control.request_id,
                        )
                        await websocket.close(code=4400)
                        is_closed = True
                        return

                    except Exception:
                        await _send_session_start_failure(
                            websocket,
                            request_id=control.request_id,
                            stage=SessionStartFailureStage.MEETING_VALIDATION,
                        )
                        is_closed = True
                        return

                    try:
                        active_session = container.get_live_transcription_session(
                            meeting_id=validated.meeting_id,
                            language_hint=validated.language_hint,
                            source=validated.source,
                        )
                    except Exception:
                        await _send_session_start_failure(
                            websocket,
                            request_id=control.request_id,
                            stage=SessionStartFailureStage.FACTORY,
                        )
                        is_closed = True
                        return

                    session_id = uuid4()
                    expected_sequence = 0
                    sequence_violations = 0
                    assist_unavailable = False
                    if control.assist_mode is not None and control.assist_mode.enabled:
                        _LOGGER.debug(
                            "assist session configuration translation=%s "
                            "simplification=%s reply_coaching=%s",
                            control.assist_mode.translation_enabled,
                            control.assist_mode.simplification_enabled,
                            control.assist_mode.reply_coaching_enabled,
                        )
                        try:
                            assist_orchestrator = (
                                container.get_assist_mode_orchestrator(
                                    configuration=AssistModeConfiguration(
                                        translation_enabled=(
                                            control.assist_mode.translation_enabled
                                        ),
                                        simplification_enabled=(
                                            control.assist_mode.simplification_enabled
                                        ),
                                        simplification_level=(
                                            control.assist_mode.simplification_level
                                        ),
                                        reply_coaching_enabled=(
                                            control.assist_mode.reply_coaching_enabled
                                        ),
                                    ),
                                    update_sink=WebSocketAssistUpdateSink(
                                        websocket=websocket,
                                        send_lock=send_lock,
                                        simplification_level=(
                                            control.assist_mode.simplification_level
                                        ),
                                    ),
                                )
                            )
                            await assist_orchestrator.start()
                        except ProviderUnavailableError:
                            _LOGGER.debug(
                                "assist initialization unavailable "
                                "capability=translation"
                            )
                            assist_orchestrator = None
                            assist_unavailable = True
                        except Exception:
                            await _send_session_start_failure(
                                websocket,
                                request_id=control.request_id,
                                stage=SessionStartFailureStage.ASSIST_INITIALIZATION,
                            )
                            is_closed = True
                            return
                    try:
                        await _send_control(
                            websocket,
                            SessionStartedMessage(
                                version=PROTOCOL_VERSION,
                                request_id=control.request_id,
                                session_id=session_id,
                            ),
                        )
                    except Exception:
                        await _send_session_start_failure(
                            websocket,
                            request_id=control.request_id,
                            stage=SessionStartFailureStage.STARTED_SEND,
                        )
                        is_closed = True
                        return
                    assist_mode = control.assist_mode
                    assist_enabled = assist_mode is not None and assist_mode.enabled
                    translation_enabled = (
                        assist_mode is not None
                        and assist_mode.enabled
                        and assist_mode.translation_enabled
                    )
                    simplification_enabled = (
                        assist_mode is not None
                        and assist_mode.enabled
                        and assist_mode.simplification_enabled
                    )
                    reply_coaching_enabled = (
                        assist_mode is not None
                        and assist_mode.enabled
                        and assist_mode.reply_coaching_enabled
                    )
                    emit_throughput(
                        "session_started",
                        assist="on" if assist_enabled else "off",
                        translation="on" if translation_enabled else "off",
                        simplification="on" if simplification_enabled else "off",
                        reply_coaching="on" if reply_coaching_enabled else "off",
                    )
                    if assist_unavailable:
                        await _send_protocol_error(
                            websocket,
                            code="assist_unavailable",
                            message="Assist Mode is unavailable.",
                            fatal=False,
                            session_id=session_id,
                            request_id=control.request_id,
                        )
                    continue

                if isinstance(control, EndSessionMessage):
                    if active_session is None or control.session_id != session_id:
                        await _send_protocol_error(
                            websocket,
                            code="invalid_session",
                            message=(
                                "The requested live transcription session is invalid."
                            ),
                            fatal=False,
                            session_id=session_id,
                            request_id=control.request_id,
                            expected_sequence=expected_sequence,
                        )
                        continue

                    expected_last_sequence = (
                        expected_sequence - 1 if expected_sequence > 0 else 0
                    )
                    if control.last_sequence != expected_last_sequence:
                        await _send_protocol_error(
                            websocket,
                            code="invalid_last_sequence",
                            message="The final chunk sequence is invalid.",
                            fatal=False,
                            session_id=session_id,
                            request_id=control.request_id,
                            expected_sequence=expected_sequence,
                        )
                        continue

                    if assist_orchestrator is not None:
                        await assist_orchestrator.stop()
                        assist_orchestrator = None
                    await active_session.stop()
                    await _send_control(
                        websocket,
                        SessionStoppedMessage(
                            version=PROTOCOL_VERSION,
                            request_id=control.request_id,
                            session_id=session_id,
                        ),
                    )
                    await websocket.close(code=1000)
                    is_closed = True
                    return

                await _send_protocol_error(
                    websocket,
                    code="unexpected_message",
                    message="The control message is not valid in the current state.",
                    fatal=False,
                    session_id=session_id,
                    expected_sequence=(
                        expected_sequence if active_session is not None else None
                    ),
                )
                continue

            data = message.get("bytes")
            if data is None:
                await _send_protocol_error(
                    websocket,
                    code="invalid_message",
                    message="The WebSocket message is invalid.",
                    fatal=False,
                )
                continue
            if active_session is None or session_id is None:
                await _send_protocol_error(
                    websocket,
                    code="session_required",
                    message="A live transcription session must be started first.",
                    fatal=False,
                )
                continue

            chunk_request_started_at = time.monotonic()
            try:
                metadata, wav_payload = parse_audio_chunk_frame(data)
            except ApplicationValidationError as error:
                _LOGGER.debug(
                    "live-transcription backend audio frame validation failed"
                )
                if "exceeds" in str(error):
                    await websocket.close(code=1009)
                    is_closed = True
                    return
                await _send_protocol_error(
                    websocket,
                    code="invalid_audio_frame",
                    message="The audio frame is invalid.",
                    fatal=True,
                    session_id=session_id,
                    expected_sequence=expected_sequence,
                )
                await websocket.close(code=4400)
                is_closed = True
                return

            _LOGGER.debug(
                "live-transcription backend audio frame validation succeeded "
                "sequence=%d",
                metadata.sequence,
            )

            if assist_orchestrator is not None:
                assist_orchestrator.set_upstream_pending_stt_chunks(
                    metadata.upstream_pending_chunks
                )

            if (
                metadata.session_id != session_id
                or metadata.sequence != expected_sequence
            ):
                sequence_violations += 1
                fatal = sequence_violations >= _MAX_SEQUENCE_VIOLATIONS
                await _send_protocol_error(
                    websocket,
                    code="unexpected_sequence",
                    message="The audio chunk sequence is not expected.",
                    fatal=fatal,
                    session_id=session_id,
                    expected_sequence=expected_sequence,
                )
                if fatal:
                    await websocket.close(code=4400)
                    is_closed = True
                    return
                continue

            chunk_received_at = time.monotonic()
            emit_throughput(
                "chunk_received",
                sequence=metadata.sequence,
                audio_duration_ms=estimate_v1_pcm16_wav_duration_ms(
                    byte_length=metadata.byte_length,
                    sample_rate_hz=metadata.sample_rate_hz,
                    channels=metadata.channels,
                ),
                frame_parse_ms=_elapsed_milliseconds(chunk_request_started_at),
                entry_monotonic_ms=_monotonic_milliseconds(chunk_request_started_at),
            )
            try:
                _LOGGER.debug(
                    "live-transcription backend audio chunk received sequence=%d",
                    metadata.sequence,
                )
                chunk = CapturedAudioChunk(
                    meeting_id=active_session.meeting_id,  # type: ignore[attr-defined]
                    sequence=metadata.sequence,
                    capture_started_at=metadata.capture_started_at,
                    audio=AudioInput(
                        data=wav_payload,
                        sample_rate_hz=metadata.sample_rate_hz,
                        channels=metadata.channels,
                        audio_format=metadata.audio_format,
                    ),
                    source=metadata.source,
                    overlap_seconds=metadata.overlap_seconds,
                )
                _LOGGER.debug(
                    "live-transcription session processing started sequence=%d",
                    metadata.sequence,
                )
                result = await active_session.process_chunk(chunk)
                emit_throughput(
                    "backend_chunk",
                    sequence=metadata.sequence,
                    audio_duration_ms=estimate_v1_pcm16_wav_duration_ms(
                        byte_length=metadata.byte_length,
                        sample_rate_hz=metadata.sample_rate_hz,
                        channels=metadata.channels,
                    ),
                    backend_queue_wait_ms=0,
                    total_backend_ms=_elapsed_milliseconds(chunk_request_started_at),
                )
                _LOGGER.debug(
                    "live-transcription session processing completed "
                    "sequence=%d accepted_count=%d elapsed_ms=%d",
                    metadata.sequence,
                    len(result.accepted_segments),
                    _elapsed_milliseconds(chunk_received_at),
                )
            except ApplicationValidationError:
                _LOGGER.debug(
                    "live-transcription session processing failed "
                    "stage=validation sequence=%d",
                    metadata.sequence,
                )
                await _send_protocol_error(
                    websocket,
                    code="invalid_audio_frame",
                    message="The audio frame is invalid.",
                    fatal=True,
                    session_id=session_id,
                    expected_sequence=expected_sequence,
                )
                await websocket.close(code=4400)
                is_closed = True
                return
            except ProviderError:
                _LOGGER.debug(
                    "live-transcription session processing failed "
                    "stage=provider sequence=%d",
                    metadata.sequence,
                )
                await _send_status(
                    websocket,
                    LiveTranscriptionStatus(
                        kind=LiveTranscriptionStatusKind.PROVIDER_ERROR,
                        message="The transcription provider is unavailable.",
                        chunk_sequence=expected_sequence,
                    ),
                )
                await _send_protocol_error(
                    websocket,
                    code="provider_error",
                    message="The transcription provider failed.",
                    fatal=False,
                    session_id=session_id,
                    expected_sequence=expected_sequence,
                )
                continue

            chunk_result_send_started_at = time.monotonic()
            _LOGGER.debug(
                "live-transcription backend chunk_result send started sequence=%d "
                "accepted_count=%d elapsed_ms=%d",
                result.chunk_sequence,
                len(result.accepted_segments),
                _elapsed_milliseconds(chunk_received_at),
            )
            await _send_chunk_result(websocket, result)
            emit_throughput(
                "chunk_result_sent",
                sequence=result.chunk_sequence,
                accepted_count=len(result.accepted_segments),
                elapsed_ms=_elapsed_milliseconds(chunk_result_send_started_at),
            )
            emit_throughput(
                "backend_chunk_completed",
                sequence=result.chunk_sequence,
                backend_total_ms=_elapsed_milliseconds(chunk_request_started_at),
            )
            _LOGGER.debug(
                "live-transcription backend chunk_result send completed "
                "sequence=%d elapsed_ms=%d",
                result.chunk_sequence,
                _elapsed_milliseconds(chunk_result_send_started_at),
            )
            if result.accepted_segments:
                _LOGGER.debug(
                    "live-transcription transcript segment accepted count=%d",
                    len(result.accepted_segments),
                )
            if result.status is not None:
                await _send_status(websocket, result.status)
            if assist_orchestrator is not None:
                for processed_segment in result.accepted_segments:
                    try:
                        accepted = await assist_orchestrator.enqueue(
                            TranscriptSegment(
                                transcript_id=processed_segment.transcript_id,
                                meeting_id=active_session.meeting_id,  # type: ignore[attr-defined]
                                text=processed_segment.text,
                                timestamp=processed_segment.timestamp,
                                source=processed_segment.source,
                                speaker=processed_segment.speaker,
                            )
                        )
                        _LOGGER.debug(
                            "assist transcript enqueue completed accepted=%s",
                            accepted,
                        )
                    except RuntimeError:
                        _LOGGER.debug("assist transcript enqueue unavailable")
                        break
            expected_sequence += 1
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        if not is_closed:
            await _send_protocol_error(
                websocket,
                code="internal_error",
                message="The live transcription connection failed.",
                fatal=True,
                session_id=session_id,
            )
            await websocket.close(code=1011)
    finally:
        if assist_orchestrator is not None:
            await assist_orchestrator.stop()
        if active_session is not None:
            await active_session.stop()


async def _receive_hello(websocket: WebSocket) -> HelloMessage:
    """Receive the required first hello message within the V1 timeout."""

    try:
        async with asyncio.timeout(_HANDSHAKE_TIMEOUT_SECONDS):
            payload = await websocket.receive_text()
    except TimeoutError as error:
        raise ProviderAuthenticationError("Sidecar authentication failed.") from error
    except RuntimeError as error:
        raise ProviderAuthenticationError("Sidecar authentication failed.") from error

    message = parse_protocol_message(payload)
    if not isinstance(message, HelloMessage):
        raise ProviderAuthenticationError("Sidecar authentication failed.")
    return message


def _get_container(websocket: WebSocket) -> Container:
    """Return the application container without widening endpoint dependencies."""

    return cast(Container, websocket.app.state.container)


def _is_loopback_client(websocket: WebSocket) -> bool:
    """Allow only IPv4/IPv6 loopback clients on this local-only endpoint."""

    client = websocket.client
    return client is not None and client.host in {"127.0.0.1", "::1"}


def _is_allowed_origin(websocket: WebSocket) -> bool:
    """Allow packaged Tauri origins and debug Vite origins, or no origin."""

    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    allowed_origins = _PACKAGED_TAURI_ORIGINS
    if _get_container(websocket).get_settings().debug:
        allowed_origins |= _DEBUG_VITE_ORIGINS
    return origin in allowed_origins


async def _parse_control_or_send_error(
    websocket: WebSocket, payload: str
) -> Any | None:
    """Parse a control message or return a nonfatal privacy-safe protocol error."""

    try:
        return parse_protocol_message(payload)
    except ApplicationValidationError:
        await _send_protocol_error(
            websocket,
            code="invalid_control_message",
            message="The control message is invalid.",
            fatal=False,
        )
        return None


async def _send_control(
    websocket: WebSocket,
    message: HelloAckMessage | SessionStartedMessage | SessionStoppedMessage,
) -> None:
    """Send an existing strict protocol control DTO."""

    await _send_text(websocket, serialize_protocol_message(message))


async def _send_protocol_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
    fatal: bool,
    session_id: UUID | None = None,
    request_id: UUID | None = None,
    expected_sequence: int | None = None,
    session_start_stage: SessionStartFailureStage | None = None,
) -> None:
    """Send a control error that deliberately omits sensitive payload details."""

    await _send_text(
        websocket,
        serialize_protocol_message(
            ProtocolErrorMessage(
                version=PROTOCOL_VERSION,
                code=code,
                message=message,
                fatal=fatal,
                session_id=session_id,
                request_id=request_id,
                expected_sequence=expected_sequence,
                session_start_stage=session_start_stage,
            )
        ),
    )


async def _send_session_start_failure(
    websocket: WebSocket,
    *,
    request_id: UUID,
    stage: SessionStartFailureStage,
) -> None:
    """Report one unexpected startup failure using a closed, privacy-safe stage."""

    try:
        await _send_protocol_error(
            websocket,
            code="session_start_failed",
            message="The live transcription session could not be started.",
            fatal=True,
            request_id=request_id,
            session_start_stage=stage,
        )
    finally:
        await websocket.close(code=1011)


async def _send_chunk_result(
    websocket: WebSocket,
    result: LiveTranscriptionChunkResult,
) -> None:
    """Send one minimal JSON result for accepted finalized transcript segments."""

    await _send_text(
        websocket,
        json.dumps(
            {
                "type": "chunk_result",
                "version": PROTOCOL_VERSION,
                "chunk_sequence": result.chunk_sequence,
                "accepted_segments": [
                    {
                        "transcript_id": str(segment.transcript_id),
                        "text": segment.text,
                        "timestamp": _serialize_timestamp(segment.timestamp),
                        "source": segment.source.value,
                        "speaker": segment.speaker,
                    }
                    for segment in result.accepted_segments
                ],
                "skipped_silence": result.skipped_silence,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


async def _send_status(websocket: WebSocket, status: LiveTranscriptionStatus) -> None:
    """Send one user-visible status without transport or provider details."""

    await _send_text(
        websocket,
        json.dumps(
            {
                "type": "status",
                "version": PROTOCOL_VERSION,
                "kind": status.kind.value,
                "message": status.message,
                "chunk_sequence": status.chunk_sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _serialize_timestamp(timestamp: datetime) -> str:
    """Render a UTC timestamp in the protocol's canonical Z form."""

    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _elapsed_milliseconds(started_at: float) -> int:
    """Return a privacy-safe monotonic elapsed duration for debug diagnostics."""

    return int((time.monotonic() - started_at) * 1_000)


def _monotonic_milliseconds(timestamp: float) -> int:
    """Render a process-local timing point without using capture timestamps."""

    return int(timestamp * 1_000)


async def _send_text(websocket: WebSocket, payload: str) -> None:
    """Serialize every connection-local outbound text frame through one lock."""

    send_lock = cast(asyncio.Lock, websocket.state.live_transcription_send_lock)
    async with send_lock:
        await websocket.send_text(payload)
