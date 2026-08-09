"""Tests for the trusted local recording capture transport."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.api.routes.recording_capture import router
from app.application.dto.recordings import (
    PrepareRecordingSessionResult,
    RecordingRetentionPolicy,
    RecordingState,
    WriteRecordingSegmentResult,
)
from app.application.exceptions import ProviderAuthenticationError
from app.infrastructure.audio import WavChunkBuilder
from fastapi import FastAPI
from fastapi.testclient import TestClient

_MEETING_ID = UUID(int=1)
_RECORDING_ID = UUID(int=2)


class _Validator:
    def validate(self, token: str) -> None:
        if token != "token":
            raise ProviderAuthenticationError("private token")


class _Prepare:
    async def execute(self, command: object) -> PrepareRecordingSessionResult:
        if not command.recording_enabled:
            return PrepareRecordingSessionResult(
                enabled=False,
                recording_id=None,
                meeting_id=command.meeting_id,
                retention_policy=None,
                expires_at=None,
            )
        return PrepareRecordingSessionResult(
            enabled=True,
            recording_id=_RECORDING_ID,
            meeting_id=command.meeting_id,
            retention_policy=RecordingRetentionPolicy.SEVEN_DAYS,
            expires_at=datetime(2026, 1, 8, tzinfo=UTC),
        )


class _Void:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def execute(self, command: object) -> None:
        self.commands.append(command)


class _Writer:
    def __init__(self) -> None:
        self.commands: list[object] = []

    async def execute(self, command: object) -> WriteRecordingSegmentResult:
        self.commands.append(command)
        return WriteRecordingSegmentResult(
            recording_id=command.recording_id,
            segment_index=command.segment_index,
        )


class _Storage:
    def __init__(self) -> None:
        self.count = 0

    async def list_segments(self, _: UUID) -> tuple[object, ...]:
        return tuple(object() for _ in range(self.count))


def _client() -> tuple[TestClient, _Writer, _Void, _Storage]:
    writer = _Writer()
    start = _Void()
    finalize = _Void()
    fail = _Void()
    storage = _Storage()
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(
        get_sidecar_token_validator=lambda: _Validator(),
        get_prepare_recording_session_use_case=lambda: _Prepare(),
        get_mark_recording_started_use_case=lambda: start,
        get_write_recording_segment_use_case=lambda: writer,
        get_finalize_recording_use_case=lambda: finalize,
        get_mark_recording_failed_use_case=lambda: fail,
        get_recording_storage=lambda: storage,
    )
    return TestClient(app), writer, finalize, storage


def _headers() -> dict[str, str]:
    return {"x-ai-meeting-copilot-token": "token"}


def _wav(*, sample_rate_hz: int = 16_000, channels: int = 1) -> bytes:
    return (
        WavChunkBuilder(sample_rate_hz=sample_rate_hz, channels=channels)
        .build(b"\x00" * (channels * 2))
        .data
    )


def test_capture_routes_authenticate_and_prepare_only_safe_fields() -> None:
    client, _, _, _ = _client()
    url = f"/api/v1/internal/recordings/{_MEETING_ID}/prepare"
    payload = {"enabled": True, "consent_confirmed_at": "2026-01-01T00:00:00Z"}

    assert client.post(url, json=payload).status_code == 401
    assert (
        client.post(
            url,
            json=payload,
            headers={**_headers(), "x-ai-meeting-copilot-token": "bad"},
        ).status_code
        == 401
    )
    enabled = client.post(url, json=payload, headers=_headers())
    disabled = client.post(url, json={"enabled": False}, headers=_headers())

    assert enabled.status_code == disabled.status_code == 200
    assert set(enabled.json()) == {
        "enabled",
        "recording_id",
        "state",
        "format",
        "expires_at",
        "protected",
    }
    assert all(
        sensitive not in enabled.text
        for sensitive in ("storage", "key_reference", "path", "token")
    )
    assert disabled.json()["enabled"] is False


def test_write_validates_wav_and_keeps_binary_private() -> None:
    client, writer, _, _ = _client()
    url = f"/api/v1/internal/recordings/{_RECORDING_ID}/segments/0"
    headers = {**_headers(), "content-type": "audio/wav"}

    valid = client.post(url, content=_wav(), headers=headers)
    malformed = client.post(url, content=b"not-wav", headers=headers)
    wrong_rate = client.post(url, content=_wav(sample_rate_hz=8_000), headers=headers)
    wrong_channels = client.post(url, content=_wav(channels=2), headers=headers)
    oversized = client.post(url, content=b"x" * (512 * 1024 + 1), headers=headers)
    empty = client.post(url, content=b"", headers=headers)

    assert valid.status_code == 200
    assert writer.commands[0].wav_bytes == _wav()
    assert all(
        response.status_code in {409, 413}
        for response in (malformed, wrong_rate, wrong_channels, oversized, empty)
    )
    assert "not-wav" not in malformed.text
    assert "storage" not in valid.text


def test_finalize_and_failure_use_safe_lifecycle_contracts() -> None:
    client, _, finalize, storage = _client()
    final_url = f"/api/v1/internal/recordings/{_RECORDING_ID}/finalize"
    fail_url = f"/api/v1/internal/recordings/{_RECORDING_ID}/fail"
    storage.count = 1

    completed = client.post(
        final_url,
        json={"duration_seconds": 5, "segment_count": 1, "has_gaps": False},
        headers=_headers(),
    )
    mismatch = client.post(
        final_url,
        json={"duration_seconds": 5, "segment_count": 2, "has_gaps": False},
        headers=_headers(),
    )
    failed = client.post(
        fail_url,
        json={"failure_code": "capture_failed"},
        headers=_headers(),
    )
    invalid_failure = client.post(
        fail_url,
        json={"failure_code": "private path"},
        headers=_headers(),
    )

    assert completed.status_code == failed.status_code == 200
    assert mismatch.status_code == invalid_failure.status_code == 409
    assert len(finalize.commands) == 1
    assert completed.json()["state"] == RecordingState.COMPLETED.value
