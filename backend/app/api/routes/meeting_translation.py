"""Safe API endpoints for versioned Turkish Meeting translations."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.application.dto.meeting_review import (
    GenerateMeetingTranslationCommand,
    GetMeetingTranslationQuery,
    MeetingTranslationArtifact,
)
from app.application.exceptions import ApplicationValidationError, ProviderError
from app.application.use_cases import (
    GenerateMeetingTranslationUseCase,
    GetMeetingTranslationUseCase,
)
from app.core.container import Container
from app.domain.value_objects import MeetingId

router = APIRouter(prefix="/api/v1/meetings")


class _GenerateTranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_regenerate: bool = False


class _TranslationSegmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript_id: UUID
    source_text: str
    translated_text: str


class _TranslationArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    meeting_id: UUID
    version: int
    target_language: str
    status: str
    created_at: str
    completed_at: str | None
    source_transcript_count: int
    segments: list[_TranslationSegmentResponse]


class _GeneratedTranslationArtifactResponse(_TranslationArtifactResponse):
    reused_existing: bool


@router.post(
    "/{meeting_id}/translation",
    response_model=_GeneratedTranslationArtifactResponse,
)
async def generate_meeting_translation(
    meeting_id: UUID,
    payload: _GenerateTranslationRequest,
    request: Request,
) -> _GeneratedTranslationArtifactResponse:
    """Generate a translation while exposing only safe artifact fields."""

    try:
        result = await _generate_use_case(request).execute(
            GenerateMeetingTranslationCommand(
                meeting_id=MeetingId(meeting_id),
                force_regenerate=payload.force_regenerate,
            )
        )
    except ProviderError as error:
        raise HTTPException(
            status_code=503,
            detail="Meeting translation is unavailable.",
        ) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Meeting not found") from error
    artifact = _artifact_response(result.artifact)
    return _GeneratedTranslationArtifactResponse(
        artifact_id=artifact.artifact_id,
        meeting_id=artifact.meeting_id,
        version=artifact.version,
        target_language=artifact.target_language,
        status=artifact.status,
        created_at=artifact.created_at,
        completed_at=artifact.completed_at,
        source_transcript_count=artifact.source_transcript_count,
        segments=artifact.segments,
        reused_existing=result.reused_existing,
    )


@router.get("/{meeting_id}/translation", response_model=_TranslationArtifactResponse)
async def get_meeting_translation(
    meeting_id: UUID,
    request: Request,
    version: int | None = Query(default=None, ge=1),
) -> _TranslationArtifactResponse:
    """Return only a completed translation artifact version."""

    try:
        result = await GetMeetingTranslationUseCase(
            _container(request).get_unit_of_work
        ).execute(
            GetMeetingTranslationQuery(
                meeting_id=MeetingId(meeting_id),
                version=version,
            )
        )
    except (ApplicationValidationError, LookupError) as error:
        raise HTTPException(
            status_code=404 if isinstance(error, LookupError) else 422,
            detail=(
                "Meeting translation not found"
                if isinstance(error, LookupError)
                else "Invalid Meeting translation query."
            ),
        ) from error
    return _artifact_response(result.artifact)


def _artifact_response(
    artifact: MeetingTranslationArtifact,
) -> _TranslationArtifactResponse:
    """Project an artifact to the intentionally narrow public API contract."""

    return _TranslationArtifactResponse(
        artifact_id=artifact.artifact_id,
        meeting_id=artifact.meeting_id.value,
        version=artifact.version,
        target_language=artifact.target_language,
        status=artifact.status.value,
        created_at=_utc_isoformat(artifact.created_at),
        completed_at=(
            None
            if artifact.completed_at is None
            else _utc_isoformat(artifact.completed_at)
        ),
        source_transcript_count=artifact.source_transcript_count,
        segments=[
            _TranslationSegmentResponse(
                transcript_id=segment.transcript_id,
                source_text=segment.source_text,
                translated_text=segment.translated_text,
            )
            for segment in artifact.segments
        ],
    )


def _generate_use_case(request: Request) -> GenerateMeetingTranslationUseCase:
    """Compose generation from existing lifecycle-managed container dependencies."""

    container = _container(request)
    settings = container.get_settings()
    return GenerateMeetingTranslationUseCase(
        unit_of_work_factory=container.get_unit_of_work,
        translation_provider=container.get_translation_provider(),
        utc_clock=_utc_now,
        uuid_factory=uuid4,
        provider_name=settings.translation_provider,
        model_name=settings.ollama_translation_model,
        prompt_version="meeting_translation_v1",
        schema_version=1,
    )


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def _utc_isoformat(value: datetime) -> str:
    """Serialize a UTC datetime using canonical ISO-8601 text."""

    return value.astimezone(UTC).isoformat()


def _container(request: Request) -> Container:
    """Return the lifespan-managed composition root."""

    return cast(Container, request.app.state.container)
