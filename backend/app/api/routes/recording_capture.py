"""Trusted loopback recording lifecycle and binary WAV segment endpoints."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.application.dto.recordings import (
    FinalizeRecordingCommand,
    MarkRecordingFailedCommand,
    MarkRecordingStartedCommand,
    PrepareRecordingSessionCommand,
    RecordingMediaFormat,
    RecordingRetentionPolicy,
    RecordingState,
    WriteRecordingSegmentCommand,
)
from app.application.exceptions import (
    ApplicationValidationError,
    ProviderAuthenticationError,
    RecordingKeyStoreError,
    RecordingStorageError,
)
from app.core.container import Container
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId
from app.infrastructure.recordings import validate_wav_recording_segment

router = APIRouter(prefix="/api/v1/internal/recordings")
MAX_RECORDING_SEGMENT_BYTES = 512 * 1024


class _PrepareRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    consent_confirmed_at: datetime | None = None
    retention_policy: RecordingRetentionPolicy = RecordingRetentionPolicy.SEVEN_DAYS


class _FinalizeRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(ge=0)
    segment_count: int = Field(ge=0)
    has_gaps: bool


class _FailRecordingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_code: str


class _RecordingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    recording_id: UUID | None
    state: RecordingState | None
    format: RecordingMediaFormat | None = None
    expires_at: datetime | None = None
    protected: bool | None = None


class _SegmentWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_id: UUID
    segment_index: int
    state: RecordingState


@router.post("/{meeting_id}/prepare", response_model=_RecordingResponse)
async def prepare_recording(
    meeting_id: UUID,
    payload: _PrepareRecordingRequest,
    request: Request,
    x_ai_meeting_copilot_token: str = Header(""),
) -> _RecordingResponse:
    """Prepare optional local recording metadata without revealing storage details."""

    container = _authenticate(request, x_ai_meeting_copilot_token)
    try:
        result = await container.get_prepare_recording_session_use_case().execute(
            PrepareRecordingSessionCommand(
                meeting_id=MeetingId(meeting_id),
                recording_enabled=payload.enabled,
                retention_policy=payload.retention_policy,
                consent_confirmed=payload.consent_confirmed_at is not None,
                consent_confirmed_at=payload.consent_confirmed_at,
            )
        )
    except LookupError as error:
        raise HTTPException(404, "Recording is unavailable.") from error
    except (ApplicationValidationError, InvalidStateTransitionError) as error:
        raise HTTPException(409, "Recording is unavailable.") from error
    except (RecordingKeyStoreError, RecordingStorageError) as error:
        raise HTTPException(503, "Recording is unavailable.") from error
    if not result.enabled:
        return _RecordingResponse(
            enabled=False,
            recording_id=None,
            state=None,
        )
    return _RecordingResponse(
        recording_id=result.recording_id,
        state=RecordingState.PENDING,
        format=RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1,
        expires_at=result.expires_at,
        protected=False,
    )


@router.post("/{recording_id}/start", response_model=_RecordingResponse)
async def start_recording(
    recording_id: UUID,
    request: Request,
    x_ai_meeting_copilot_token: str = Header(""),
) -> _RecordingResponse:
    """Mark a prepared recording active at the backend's UTC capture anchor."""

    container = _authenticate(request, x_ai_meeting_copilot_token)
    try:
        await container.get_mark_recording_started_use_case().execute(
            MarkRecordingStartedCommand(
                recording_id=recording_id,
                capture_anchor_utc=datetime.now(UTC),
            )
        )
    except LookupError as error:
        raise HTTPException(404, "Recording is unavailable.") from error
    except (ApplicationValidationError, InvalidStateTransitionError) as error:
        raise HTTPException(409, "Recording is unavailable.") from error
    return _RecordingResponse(
        recording_id=recording_id,
        state=RecordingState.RECORDING,
    )


@router.post(
    "/{recording_id}/segments/{segment_index}", response_model=_SegmentWriteResponse
)
async def write_recording_segment(
    recording_id: UUID,
    segment_index: int,
    request: Request,
    x_ai_meeting_copilot_token: str = Header(""),
) -> _SegmentWriteResponse:
    """Validate and persist one bounded WAV segment through encrypted storage."""

    container = _authenticate(request, x_ai_meeting_copilot_token)
    _require_binary_content_type(request)
    if segment_index < 0:
        raise HTTPException(422, "Recording segment was rejected.")
    wav_bytes = await request.body()
    if not wav_bytes or len(wav_bytes) > MAX_RECORDING_SEGMENT_BYTES:
        raise HTTPException(413, "Recording segment was rejected.")
    try:
        validate_wav_recording_segment(wav_bytes)
        result = await container.get_write_recording_segment_use_case().execute(
            WriteRecordingSegmentCommand(
                recording_id=recording_id,
                segment_index=segment_index,
                wav_bytes=wav_bytes,
            )
        )
    except LookupError as error:
        raise HTTPException(404, "Recording is unavailable.") from error
    except (
        ApplicationValidationError,
        InvalidStateTransitionError,
        RecordingStorageError,
        RecordingKeyStoreError,
    ) as error:
        raise HTTPException(409, "Recording segment was rejected.") from error
    return _SegmentWriteResponse(
        recording_id=result.recording_id,
        segment_index=result.segment_index,
        state=RecordingState.RECORDING,
    )


@router.post("/{recording_id}/finalize", response_model=_RecordingResponse)
async def finalize_recording(
    recording_id: UUID,
    payload: _FinalizeRecordingRequest,
    request: Request,
    x_ai_meeting_copilot_token: str = Header(""),
) -> _RecordingResponse:
    """Finalize metadata only after the supplied count matches encrypted storage."""

    container = _authenticate(request, x_ai_meeting_copilot_token)
    try:
        persisted_segments = await container.get_recording_storage().list_segments(
            recording_id
        )
        if len(persisted_segments) != payload.segment_count:
            raise ApplicationValidationError("Recording segment count is invalid.")
        await container.get_finalize_recording_use_case().execute(
            FinalizeRecordingCommand(
                recording_id=recording_id,
                duration_seconds=payload.duration_seconds,
                segment_count=payload.segment_count,
                has_gaps=payload.has_gaps,
            )
        )
    except LookupError as error:
        raise HTTPException(404, "Recording is unavailable.") from error
    except (
        ApplicationValidationError,
        InvalidStateTransitionError,
        RecordingStorageError,
    ) as error:
        raise HTTPException(409, "Recording is unavailable.") from error
    return _RecordingResponse(
        recording_id=recording_id,
        state=RecordingState.COMPLETED,
    )


@router.post("/{recording_id}/fail", response_model=_RecordingResponse)
async def fail_recording(
    recording_id: UUID,
    payload: _FailRecordingRequest,
    request: Request,
    x_ai_meeting_copilot_token: str = Header(""),
) -> _RecordingResponse:
    """Persist one allowlisted recording failure code without native details."""

    container = _authenticate(request, x_ai_meeting_copilot_token)
    try:
        await container.get_mark_recording_failed_use_case().execute(
            MarkRecordingFailedCommand(
                recording_id=recording_id,
                failure_code=payload.failure_code,
            )
        )
    except LookupError as error:
        raise HTTPException(404, "Recording is unavailable.") from error
    except (ApplicationValidationError, InvalidStateTransitionError) as error:
        raise HTTPException(409, "Recording is unavailable.") from error
    return _RecordingResponse(recording_id=recording_id, state=RecordingState.FAILED)


def _authenticate(request: Request, token: str) -> Container:
    """Authenticate the trusted loopback desktop sidecar token."""

    container = cast(Container, request.app.state.container)
    try:
        container.get_sidecar_token_validator().validate(token)
    except (ProviderAuthenticationError, RuntimeError) as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Recording is unavailable.",
        ) from error
    return container


def _require_binary_content_type(request: Request) -> None:
    """Accept only the two V1 raw-binary content types."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"audio/wav", "application/octet-stream"}:
        raise HTTPException(415, "Recording segment was rejected.")
