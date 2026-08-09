"""Safe API endpoints for completed versioned Meeting reviews."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.application.dto.meeting_review import (
    GenerateMeetingReviewCommand,
    GetMeetingReviewQuery,
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
)
from app.application.exceptions import ApplicationValidationError, ProviderError
from app.core.container import Container
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId

router = APIRouter(prefix="/api/v1/meetings")


class _GenerateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_regenerate: bool = False


class _ActionItemResponse(BaseModel):
    text: str
    owner: str | None
    due_date: str | None


class _OpenQuestionResponse(BaseModel):
    question: str


class _TechnicalQuestionResponse(BaseModel):
    question: str
    answer_summary: str | None
    evaluation: str | None
    improvement_suggestion: str | None


class _TechnicalTermResponse(BaseModel):
    term: str
    explanation: str


class _FeedbackResponse(BaseModel):
    strengths: list[str]
    improvement_areas: list[str]
    overall_feedback: str


class _ReviewContentResponse(BaseModel):
    summary: str
    key_decisions: list[str]
    action_items: list[_ActionItemResponse]
    open_questions: list[_OpenQuestionResponse]
    technical_questions: list[_TechnicalQuestionResponse]
    technical_terms: list[_TechnicalTermResponse]
    feedback: _FeedbackResponse | None


class _ReviewArtifactResponse(BaseModel):
    artifact_id: UUID
    meeting_id: UUID
    version: int
    review_type: str
    status: str
    created_at: str
    completed_at: str | None
    source_transcript_count: int
    content: _ReviewContentResponse


class _GeneratedReviewArtifactResponse(_ReviewArtifactResponse):
    reused_existing: bool


@router.post("/{meeting_id}/review", response_model=_GeneratedReviewArtifactResponse)
async def generate_meeting_review(
    meeting_id: UUID,
    payload: _GenerateReviewRequest,
    request: Request,
) -> _GeneratedReviewArtifactResponse:
    """Generate one completed review while exposing only safe artifact fields."""

    try:
        use_case = _container(request).get_generate_meeting_review_use_case()
        result = await use_case.execute(
            GenerateMeetingReviewCommand(
                meeting_id=MeetingId(meeting_id),
                force_regenerate=payload.force_regenerate,
            )
        )
    except ProviderError as error:
        raise HTTPException(
            status_code=503,
            detail="Meeting review is unavailable.",
        ) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Meeting not found") from error
    except InvalidStateTransitionError as error:
        raise HTTPException(
            status_code=409,
            detail="Meeting review is unavailable.",
        ) from error

    artifact = _artifact_response(result.artifact)
    return _GeneratedReviewArtifactResponse(
        **artifact.model_dump(), reused_existing=result.reused_existing
    )


@router.get("/{meeting_id}/review", response_model=_ReviewArtifactResponse)
async def get_meeting_review(
    meeting_id: UUID,
    request: Request,
    version: int | None = Query(default=None, ge=1),
) -> _ReviewArtifactResponse:
    """Return only a completed public-readable Meeting review version."""

    try:
        result = (
            await _container(request)
            .get_get_meeting_review_use_case()
            .execute(
                GetMeetingReviewQuery(meeting_id=MeetingId(meeting_id), version=version)
            )
        )
    except (ApplicationValidationError, LookupError) as error:
        raise HTTPException(
            status_code=404 if isinstance(error, LookupError) else 422,
            detail=(
                "Meeting review not found"
                if isinstance(error, LookupError)
                else "Invalid Meeting review query."
            ),
        ) from error
    return _artifact_response(result.artifact)


def _artifact_response(artifact: MeetingReviewArtifact) -> _ReviewArtifactResponse:
    """Project a completed artifact to the intentionally narrow public contract."""

    if (
        artifact.status is not MeetingReviewArtifactStatus.COMPLETED
        or artifact.content is None
    ):
        raise RuntimeError("Completed review artifact is required.")
    content = artifact.content
    return _ReviewArtifactResponse(
        artifact_id=artifact.artifact_id,
        meeting_id=artifact.meeting_id.value,
        version=artifact.version,
        review_type=artifact.review_type,
        status=artifact.status.value,
        created_at=_utc_isoformat(artifact.created_at),
        completed_at=_utc_isoformat(artifact.completed_at),
        source_transcript_count=artifact.source_transcript_count,
        content=_ReviewContentResponse(
            summary=content.summary,
            key_decisions=list(content.key_decisions),
            action_items=[
                _ActionItemResponse(
                    text=item.text,
                    owner=item.owner,
                    due_date=item.due_date,
                )
                for item in content.action_items
            ],
            open_questions=[
                _OpenQuestionResponse(question=item.question)
                for item in content.open_questions
            ],
            technical_questions=[
                _TechnicalQuestionResponse(
                    question=item.question,
                    answer_summary=item.answer_summary,
                    evaluation=item.evaluation,
                    improvement_suggestion=item.improvement_suggestion,
                )
                for item in content.technical_questions
            ],
            technical_terms=[
                _TechnicalTermResponse(term=item.term, explanation=item.explanation)
                for item in content.technical_terms
            ],
            feedback=(
                None
                if content.feedback is None
                else _FeedbackResponse(
                    strengths=list(content.feedback.strengths),
                    improvement_areas=list(content.feedback.improvement_areas),
                    overall_feedback=content.feedback.overall_feedback,
                )
            ),
        ),
    )


def _utc_isoformat(value: datetime | None) -> str:
    """Serialize a known UTC timestamp using canonical ISO-8601 text."""

    if value is None:
        raise RuntimeError("Completed review timestamp is required.")
    return value.astimezone(UTC).isoformat()


def _container(request: Request) -> Container:
    """Return the lifespan-managed composition root."""

    return cast(Container, request.app.state.container)
