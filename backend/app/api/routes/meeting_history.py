"""Loopback read endpoints for Meeting history and full transcripts."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.application.dto.meeting_review import MeetingDetail, MeetingHistoryItem
from app.application.exceptions import ApplicationValidationError
from app.core.container import Container
from app.domain.value_objects import MeetingId

router = APIRouter(prefix="/api/v1/meetings")


class _TranscriptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript_id: UUID
    text: str
    timestamp: str
    speaker: str
    source: str


class _HistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meeting_id: UUID
    title: str
    status: str
    created_at: str
    started_at: str | None
    ended_at: str | None
    transcript_count: int
    recording_available: bool
    recording_state: str | None
    audio_expires_at: str | None
    audio_protected: bool


class _DetailResponse(_HistoryResponse):
    transcript: list[_TranscriptResponse]
    recording_duration_seconds: float | None
    audio_has_gaps: bool


class MeetingHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meetings: list[_HistoryResponse]


@router.get("", response_model=MeetingHistoryResponse)
async def list_meetings(
    request: Request,
    limit: int = Query(default=100),
    offset: int = Query(default=0),
) -> MeetingHistoryResponse:
    try:
        items = (
            await _container(request)
            .get_list_meetings_use_case()
            .execute(limit=limit, offset=offset)
        )
    except ApplicationValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="Invalid Meeting history query.",
        ) from error
    return MeetingHistoryResponse(meetings=[_history(item) for item in items])


@router.get("/{meeting_id}", response_model=_DetailResponse)
async def get_meeting_detail(meeting_id: UUID, request: Request) -> _DetailResponse:
    try:
        detail = (
            await _container(request)
            .get_get_meeting_detail_use_case()
            .execute(meeting_id=MeetingId(meeting_id))
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Meeting not found") from error
    return _detail(detail)


def _history(item: MeetingHistoryItem) -> _HistoryResponse:
    return _HistoryResponse(
        meeting_id=item.meeting_id.value,
        title=item.title,
        status=item.status.value,
        created_at=item.created_at.isoformat(),
        started_at=None if item.started_at is None else item.started_at.isoformat(),
        ended_at=None if item.ended_at is None else item.ended_at.isoformat(),
        transcript_count=item.transcript_count,
        recording_available=item.recording_available,
        recording_state=(
            None if item.recording_state is None else item.recording_state.value
        ),
        audio_expires_at=(
            None if item.audio_expires_at is None else item.audio_expires_at.isoformat()
        ),
        audio_protected=item.audio_protected,
    )


def _detail(item: MeetingDetail) -> _DetailResponse:
    return _DetailResponse(
        meeting_id=item.meeting_id.value,
        title=item.title,
        status=item.status.value,
        created_at=item.created_at.isoformat(),
        started_at=None if item.started_at is None else item.started_at.isoformat(),
        ended_at=None if item.ended_at is None else item.ended_at.isoformat(),
        transcript_count=len(item.transcript),
        recording_available=item.recording_available,
        recording_state=(
            None if item.recording_state is None else item.recording_state.value
        ),
        audio_expires_at=(
            None if item.audio_expires_at is None else item.audio_expires_at.isoformat()
        ),
        audio_protected=item.audio_protected,
        transcript=[
            _TranscriptResponse(
                transcript_id=entry.transcript_id,
                text=entry.text,
                timestamp=entry.timestamp.isoformat(),
                speaker=entry.speaker,
                source=entry.source.value,
            )
            for entry in item.transcript
        ],
        recording_duration_seconds=item.recording_duration_seconds,
        audio_has_gaps=item.audio_has_gaps,
    )


def _container(request: Request) -> Container:
    return cast(Container, request.app.state.container)
