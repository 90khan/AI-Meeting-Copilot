"""Tests for the loopback live-transcription WebSocket boundary."""

# ruff: noqa: PT012, SIM117

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from app.api.live_transcription.binary_frames import (
    AudioChunkFrameMetadata,
    build_audio_chunk_frame,
)
from app.api.live_transcription.protocol import (
    EndSessionMessage,
    HelloMessage,
    StartSessionMessage,
    serialize_protocol_message,
)
from app.api.routes.live_transcription import _receive_hello, router
from app.application.dto import (
    AudioSource,
    LiveTranscriptionChunkResult,
    LiveTranscriptionStatus,
    LiveTranscriptionStatusKind,
    ProcessedTranscriptSegment,
)
from app.application.dto.ai import AudioFormat, LanguageCode
from app.application.dto.start_live_transcription_session_result import (
    StartLiveTranscriptionSessionResult,
)
from app.application.exceptions import ProviderAuthenticationError, ProviderError
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_PATH = "/api/v1/live-transcription"
_MEETING_ID = MeetingId(UUID("11111111-1111-1111-1111-111111111111"))
_CLIENT_ID = UUID("22222222-2222-2222-2222-222222222222")
_REQUEST_ID = UUID("33333333-3333-3333-3333-333333333333")
_TRANSCRIPT_ID = UUID("44444444-4444-4444-4444-444444444444")


class FakeValidator:
    """Token validator fake with a fixed accepted value."""

    def validate(self, supplied_token: str) -> None:
        """Reject any token other than the local test token."""

        if supplied_token != "valid-token":
            raise ProviderAuthenticationError("Sidecar authentication failed.")


class FakeStartUseCase:
    """Session-start validation fake with command tracking."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Initialize an optional validation outcome."""

        self.failure = failure
        self.commands: list[object] = []

    async def execute(self, command: object) -> StartLiveTranscriptionSessionResult:
        """Return validated configuration or raise the configured error."""

        self.commands.append(command)
        if self.failure is not None:
            raise self.failure
        return StartLiveTranscriptionSessionResult(
            meeting_id=command.meeting_id,  # type: ignore[attr-defined]
            language_hint=command.language_hint,  # type: ignore[attr-defined]
            source=command.source,  # type: ignore[attr-defined]
        )


class FakeSession:
    """Live session fake with configured result outcomes and stop tracking."""

    def __init__(
        self,
        *,
        meeting_id: MeetingId,
        language_hint: LanguageCode | None,
        source: AudioSource,
        outcomes: list[LiveTranscriptionChunkResult | Exception],
    ) -> None:
        """Bind the fake to its session-level configuration."""

        self.meeting_id = meeting_id
        self.language_hint = language_hint
        self.source = source
        self._outcomes = outcomes
        self.chunks: list[object] = []
        self.stop_call_count = 0

    async def process_chunk(self, chunk: object) -> LiveTranscriptionChunkResult:
        """Record the chunk and return the configured result."""

        self.chunks.append(chunk)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def stop(self) -> None:
        """Track endpoint cleanup calls."""

        self.stop_call_count += 1


class FakeContainer:
    """Container fake exposing only the endpoint's public dependencies."""

    def __init__(
        self,
        *,
        start_failure: Exception | None = None,
        outcomes: list[LiveTranscriptionChunkResult | Exception] | None = None,
        debug: bool = False,
    ) -> None:
        """Initialize fake lifecycle dependencies and configuration tracking."""

        self.validator = FakeValidator()
        self.start_use_case = FakeStartUseCase(start_failure)
        self.outcomes = outcomes or [_result(0)]
        self.debug = debug
        self.sessions: list[FakeSession] = []

    def get_settings(self) -> SimpleNamespace:
        """Return the minimal origin-policy configuration."""

        return SimpleNamespace(debug=self.debug)

    def get_sidecar_token_validator(self) -> FakeValidator:
        """Return the endpoint token validator boundary."""

        return self.validator

    def get_start_live_transcription_session_use_case(self) -> FakeStartUseCase:
        """Return the validation-only session-start use case."""

        return self.start_use_case

    def get_live_transcription_session(
        self,
        *,
        meeting_id: MeetingId,
        language_hint: LanguageCode | None,
        source: AudioSource,
    ) -> FakeSession:
        """Create a configured session fake for this transport connection."""

        session = FakeSession(
            meeting_id=meeting_id,
            language_hint=language_hint,
            source=source,
            outcomes=self.outcomes,
        )
        self.sessions.append(session)
        return session


def _app(container: FakeContainer) -> FastAPI:
    app = FastAPI()
    app.state.container = container
    app.include_router(router)
    return app


def _hello(token: str = "valid-token") -> str:
    return serialize_protocol_message(
        HelloMessage(version=1, token=token, client_id=_CLIENT_ID)
    )


def _start() -> str:
    return serialize_protocol_message(
        StartSessionMessage(
            version=1,
            request_id=_REQUEST_ID,
            meeting_id=_MEETING_ID,
            language_hint=LanguageCode(value="de-DE"),
            source=AudioSource.MIXED,
        )
    )


def _end(session_id: UUID, last_sequence: int) -> str:
    return serialize_protocol_message(
        EndSessionMessage(
            version=1,
            request_id=_REQUEST_ID,
            session_id=session_id,
            last_sequence=last_sequence,
        )
    )


def _result(
    sequence: int, *, status: LiveTranscriptionStatus | None = None
) -> LiveTranscriptionChunkResult:
    return LiveTranscriptionChunkResult(
        chunk_sequence=sequence,
        accepted_segments=(
            ProcessedTranscriptSegment(
                transcript_id=_TRANSCRIPT_ID,
                text="Accepted transcript",
                timestamp=datetime.now(UTC),
                source=AudioSource.MIXED,
            ),
        ),
        skipped_silence=False,
        status=status,
    )


def _frame(session_id: UUID, sequence: int, payload: bytes = b"wav") -> bytes:
    return build_audio_chunk_frame(
        metadata=AudioChunkFrameMetadata(
            session_id=session_id,
            sequence=sequence,
            capture_started_at=datetime.now(UTC),
            source=AudioSource.MIXED,
            audio_format=AudioFormat.WAV,
            sample_rate_hz=16_000,
            channels=1,
            overlap_seconds=0.5,
            byte_length=len(payload),
        ),
        wav_payload=payload,
    )


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def test_successful_hello_start_chunk_and_end_flow() -> None:
    """The endpoint authenticates, processes sequential audio, and cleans up."""

    container = FakeContainer()
    with _client(_app(container)) as client:
        with client.websocket_connect(_PATH) as websocket:
            websocket.send_text(_hello())
            assert json.loads(websocket.receive_text())["type"] == "hello_ack"

            websocket.send_text(_start())
            started = json.loads(websocket.receive_text())
            assert started["type"] == "session_started"
            session_id = UUID(started["session_id"])

            websocket.send_bytes(_frame(session_id, 0))
            result = json.loads(websocket.receive_text())
            assert result["type"] == "chunk_result"
            assert result["chunk_sequence"] == 0
            assert result["accepted_segments"][0]["transcript_id"] == str(
                _TRANSCRIPT_ID
            )

            websocket.send_text(_end(session_id, 0))
            assert json.loads(websocket.receive_text())["type"] == "session_stopped"

    assert len(container.sessions) == 1
    assert container.sessions[0].language_hint == LanguageCode(value="de-DE")
    assert container.sessions[0].source is AudioSource.MIXED
    assert len(container.sessions[0].chunks) == 1
    assert container.sessions[0].stop_call_count >= 1


def test_invalid_token_closes_with_4401() -> None:
    """Authentication failures do not disclose token details."""

    with _client(_app(FakeContainer())) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(_PATH) as websocket:
                websocket.send_text(_hello("incorrect-token"))
                websocket.receive_text()

    assert error.value.code == 4401
    assert "incorrect-token" not in str(error.value)


def test_forbidden_origin_closes_with_4403() -> None:
    """Non-allowlisted origins are rejected before session allocation."""

    with _client(_app(FakeContainer())) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                _PATH, headers={"origin": "https://bad.test"}
            ):
                pass

    assert error.value.code == 4403


def test_binary_before_session_start_is_rejected_without_processing() -> None:
    """Audio frames cannot allocate or process a session implicitly."""

    container = FakeContainer()
    with _client(_app(container)) as client:
        with client.websocket_connect(_PATH) as websocket:
            websocket.send_text(_hello())
            websocket.receive_text()
            websocket.send_bytes(b"not-an-audio-frame")
            error = json.loads(websocket.receive_text())

    assert error["code"] == "session_required"
    assert container.sessions == []


def test_session_start_validation_failure_is_fatal() -> None:
    """A rejected Meeting state sends no session allocation and closes safely."""

    container = FakeContainer(
        start_failure=InvalidStateTransitionError("internal state detail")
    )
    with _client(_app(container)) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(_PATH) as websocket:
                websocket.send_text(_hello())
                websocket.receive_text()
                websocket.send_text(_start())
                response = json.loads(websocket.receive_text())
                assert response["code"] == "session_start_rejected"
                websocket.receive_text()

    assert error.value.code == 4400
    assert container.sessions == []
    assert "internal state detail" not in response["message"]


def test_sequence_violations_do_not_process_chunks_and_close_after_three() -> None:
    """Duplicate or out-of-order chunks remain non-persistent until the limit."""

    container = FakeContainer()
    with _client(_app(container)) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(_PATH) as websocket:
                websocket.send_text(_hello())
                websocket.receive_text()
                websocket.send_text(_start())
                session_id = UUID(json.loads(websocket.receive_text())["session_id"])
                for _ in range(3):
                    websocket.send_bytes(_frame(session_id, 1))
                    assert (
                        json.loads(websocket.receive_text())["code"]
                        == "unexpected_sequence"
                    )
                websocket.receive_text()

    assert error.value.code == 4400
    assert container.sessions[0].chunks == []


def test_provider_error_is_safe_and_keeps_connection_usable() -> None:
    """Provider failures emit generic status/error messages without closing."""

    container = FakeContainer(
        outcomes=[ProviderError("sensitive provider response"), _result(0)]
    )
    with _client(_app(container)) as client:
        with client.websocket_connect(_PATH) as websocket:
            websocket.send_text(_hello())
            websocket.receive_text()
            websocket.send_text(_start())
            session_id = UUID(json.loads(websocket.receive_text())["session_id"])

            websocket.send_bytes(_frame(session_id, 0))
            status = json.loads(websocket.receive_text())
            error = json.loads(websocket.receive_text())
            assert status["kind"] == "provider_error"
            assert error["code"] == "provider_error"
            assert "sensitive provider response" not in status["message"]
            assert "sensitive provider response" not in error["message"]

            websocket.send_bytes(_frame(session_id, 0))
            assert json.loads(websocket.receive_text())["type"] == "chunk_result"


def test_status_is_emitted_after_a_chunk_result() -> None:
    """Result status is serialized separately after its corresponding chunk result."""

    status = LiveTranscriptionStatus(
        kind=LiveTranscriptionStatusKind.GAP,
        message="An audio chunk was dropped because transcription fell behind.",
        chunk_sequence=0,
    )
    container = FakeContainer(outcomes=[_result(0, status=status)])
    with _client(_app(container)) as client:
        with client.websocket_connect(_PATH) as websocket:
            websocket.send_text(_hello())
            websocket.receive_text()
            websocket.send_text(_start())
            session_id = UUID(json.loads(websocket.receive_text())["session_id"])
            websocket.send_bytes(_frame(session_id, 0))

            assert json.loads(websocket.receive_text())["type"] == "chunk_result"
            assert json.loads(websocket.receive_text())["type"] == "status"


def test_oversized_payload_closes_with_1009() -> None:
    """Frames exceeding the protocol payload cap are rejected before processing."""

    payload = b"x" * 524_289
    container = FakeContainer()
    with _client(_app(container)) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(_PATH) as websocket:
                websocket.send_text(_hello())
                websocket.receive_text()
                websocket.send_text(_start())
                session_id = UUID(json.loads(websocket.receive_text())["session_id"])
                websocket.send_bytes(_frame(session_id, 0, payload))
                websocket.receive_text()

    assert error.value.code == 1009
    assert container.sessions[0].chunks == []


def test_handshake_timeout_seam_maps_to_authentication_failure() -> None:
    """The hello receive helper enforces the required bounded handshake window."""

    class SlowWebSocket:
        async def receive_text(self) -> str:
            await asyncio.sleep(0.01)
            return _hello()

    import app.api.routes.live_transcription as route_module

    original_timeout = route_module._HANDSHAKE_TIMEOUT_SECONDS
    route_module._HANDSHAKE_TIMEOUT_SECONDS = 0.001
    try:
        with pytest.raises(ProviderAuthenticationError):
            asyncio.run(_receive_hello(SlowWebSocket()))  # type: ignore[arg-type]
    finally:
        route_module._HANDSHAKE_TIMEOUT_SECONDS = original_timeout
