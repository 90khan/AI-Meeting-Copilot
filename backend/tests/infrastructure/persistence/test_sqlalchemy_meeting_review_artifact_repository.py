"""Tests for SQLAlchemy Meeting review artifact persistence."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
    ReviewActionItem,
    ReviewFeedback,
    ReviewInterviewQuestion,
    ReviewOpenQuestion,
    ReviewTechnicalTerm,
)
from app.domain.value_objects import MeetingId
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy import (
    meeting_review_artifact_repository,
)
from app.infrastructure.persistence.sqlalchemy.models import MeetingModel
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000011"))
_OTHER_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000012"))
SQLAlchemyMeetingReviewArtifactRepository = (
    meeting_review_artifact_repository.SQLAlchemyMeetingReviewArtifactRepository
)


def _content(*, feedback: ReviewFeedback | None = None) -> MeetingReviewContent:
    return MeetingReviewContent(
        summary="Structured summary.",
        key_decisions=("First decision.", "Second decision."),
        action_items=(
            ReviewActionItem(
                text="Send the proposal.",
                owner="Owner",
                due_date="2026-02-01",
            ),
            ReviewActionItem(text="Review notes.", owner=None, due_date=None),
        ),
        open_questions=(ReviewOpenQuestion(question="Who approves it?"),),
        technical_questions=(
            ReviewInterviewQuestion(
                question="How does caching work?",
                answer_summary="It reduces repeated work.",
                evaluation="Accurate.",
                improvement_suggestion="Mention invalidation.",
            ),
        ),
        technical_terms=(
            ReviewTechnicalTerm(term="Cache", explanation="A fast data store."),
        ),
        feedback=feedback,
    )


def _artifact(
    number: int,
    *,
    meeting_id: MeetingId = _MEETING_ID,
    version: int | None = None,
    status: MeetingReviewArtifactStatus = MeetingReviewArtifactStatus.COMPLETED,
    created_at: datetime | None = None,
    feedback: ReviewFeedback | None = None,
) -> MeetingReviewArtifact:
    completed = status is MeetingReviewArtifactStatus.COMPLETED
    return MeetingReviewArtifact(
        artifact_id=UUID(int=200 + number),
        meeting_id=meeting_id,
        version=number if version is None else version,
        review_type="interview_review",
        status=status,
        created_at=created_at or datetime(2026, 2, number, tzinfo=UTC),
        completed_at=(datetime(2026, 2, number, 1, tzinfo=UTC) if completed else None),
        source_transcript_count=5 if completed else 0,
        content=_content(feedback=feedback) if completed else None,
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_review_v1",
        schema_version=1,
        failure_code=(
            "review_provider_failed"
            if status is MeetingReviewArtifactStatus.FAILED
            else None
        ),
    )


def _session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            MeetingModel(id=str(_MEETING_ID), name="First", status="active"),
            MeetingModel(id=str(_OTHER_MEETING_ID), name="Second", status="active"),
        ]
    )
    session.flush()
    return session, engine


def test_round_trip_preserves_full_nested_content_and_ordering() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingReviewArtifactRepository(session)
    feedback = ReviewFeedback(
        strengths=("Clear communication.", "Good structure."),
        improvement_areas=("Add detail.",),
        overall_feedback="Strong overall performance.",
    )
    artifact = _artifact(1, feedback=feedback)

    async def exercise() -> MeetingReviewArtifact | None:
        await repository.save(artifact)
        return await repository.get_by_id(artifact.artifact_id)

    loaded = asyncio.run(exercise())

    assert loaded == artifact
    assert loaded is not None
    assert loaded.content is not None
    assert isinstance(loaded.content.action_items, tuple)
    assert [item.text for item in loaded.content.action_items] == [
        "Send the proposal.",
        "Review notes.",
    ]
    assert loaded.content.feedback == feedback
    assert session.in_transaction()
    session.rollback()
    session.close()
    engine.dispose()


def test_round_trip_preserves_optional_feedback_and_action_item_values() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingReviewArtifactRepository(session)
    artifact = _artifact(1)

    async def exercise() -> MeetingReviewArtifact | None:
        await repository.save(artifact)
        return await repository.get_by_id(artifact.artifact_id)

    loaded = asyncio.run(exercise())

    assert loaded is not None
    assert loaded.content is not None
    assert loaded.content.feedback is None
    assert loaded.content.action_items[1].owner is None
    assert loaded.content.action_items[1].due_date is None
    session.rollback()
    session.close()
    engine.dispose()


def test_save_updates_one_lifecycle_row_without_commit_ownership() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingReviewArtifactRepository(session)
    processing = _artifact(1, status=MeetingReviewArtifactStatus.PROCESSING)
    completed = _artifact(1)

    async def exercise() -> MeetingReviewArtifact | None:
        await repository.save(processing)
        await repository.save(completed)
        return await repository.get_by_id(completed.artifact_id)

    loaded = asyncio.run(exercise())

    assert loaded == completed
    assert session.in_transaction()
    session.rollback()
    session.close()
    engine.dispose()


def test_latest_queries_include_all_versions_and_completed_only() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingReviewArtifactRepository(session)
    completed = _artifact(1)
    failed = _artifact(2, status=MeetingReviewArtifactStatus.FAILED)
    cancelled = _artifact(3, status=MeetingReviewArtifactStatus.CANCELLED)

    async def exercise() -> tuple[int, MeetingReviewArtifact | None]:
        await repository.save(completed)
        await repository.save(failed)
        await repository.save(cancelled)
        return (
            await repository.get_latest_version(_MEETING_ID, "interview_review"),
            await repository.get_latest_completed(_MEETING_ID, "interview_review"),
        )

    latest_version, latest_completed = asyncio.run(exercise())

    assert latest_version == 3
    assert latest_completed == completed
    session.rollback()
    session.close()
    engine.dispose()


def test_lookup_list_order_and_missing_results_are_deterministic() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingReviewArtifactRepository(session)
    same_time = datetime(2026, 2, 1, tzinfo=UTC)
    first = _artifact(1, created_at=same_time)
    second = _artifact(2, created_at=same_time)
    other = _artifact(3, meeting_id=_OTHER_MEETING_ID)

    async def exercise() -> tuple[
        MeetingReviewArtifact | None,
        MeetingReviewArtifact | None,
        tuple[MeetingReviewArtifact, ...],
    ]:
        await repository.save(first)
        await repository.save(second)
        await repository.save(other)
        return (
            await repository.get_by_meeting_and_version(
                _MEETING_ID,
                "interview_review",
                2,
            ),
            await repository.get_by_id(UUID(int=999)),
            await repository.list_for_meeting(_MEETING_ID, "interview_review"),
        )

    exact, missing, artifacts = asyncio.run(exercise())

    assert exact == second
    assert missing is None
    assert [artifact.artifact_id for artifact in artifacts] == [
        second.artifact_id,
        first.artifact_id,
    ]
    assert isinstance(artifacts, tuple)
    session.rollback()
    session.close()
    engine.dispose()


def test_database_enforces_unique_meeting_review_type_version() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingReviewArtifactRepository(session)
    first = _artifact(1, version=1)
    duplicate = _artifact(2, version=1)

    async def exercise() -> None:
        await repository.save(first)
        with pytest.raises(IntegrityError):
            await repository.save(duplicate)

    asyncio.run(exercise())

    session.rollback()
    session.close()
    engine.dispose()
