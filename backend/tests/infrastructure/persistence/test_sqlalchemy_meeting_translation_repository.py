"""Tests for SQLAlchemy Meeting translation artifact persistence."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    MeetingTranslationArtifact,
    TranslationArtifactSegment,
    TranslationArtifactStatus,
)
from app.domain.value_objects import MeetingId
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.meeting_translation_repository import (
    SQLAlchemyMeetingTranslationRepository,
)
from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    meeting_translation_artifact_model,
)
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

MeetingTranslationArtifactModel = (
    meeting_translation_artifact_model.MeetingTranslationArtifactModel
)

_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000001"))
_OTHER_MEETING_ID = MeetingId(UUID("00000000-0000-0000-0000-000000000002"))


def _artifact(
    artifact_number: int,
    *,
    meeting_id: MeetingId = _MEETING_ID,
    version: int | None = None,
    created_at: datetime | None = None,
    status: TranslationArtifactStatus = TranslationArtifactStatus.COMPLETED,
) -> MeetingTranslationArtifact:
    completed = status is TranslationArtifactStatus.COMPLETED
    return MeetingTranslationArtifact(
        artifact_id=UUID(int=100 + artifact_number),
        meeting_id=meeting_id,
        version=artifact_number if version is None else version,
        target_language="tr",
        status=status,
        created_at=created_at or datetime(2026, 1, artifact_number, tzinfo=UTC),
        completed_at=(
            datetime(2026, 1, artifact_number, 1, tzinfo=UTC) if completed else None
        ),
        source_transcript_count=2 if completed else 0,
        segments=(
            (
                TranslationArtifactSegment(
                    transcript_id=UUID(int=artifact_number * 10 + 1),
                    source_text=f"First source {artifact_number}",
                    translated_text=f"First translation {artifact_number}",
                ),
                TranslationArtifactSegment(
                    transcript_id=UUID(int=artifact_number * 10 + 2),
                    source_text=f"Second source {artifact_number}",
                    translated_text=f"Second translation {artifact_number}",
                ),
            )
            if completed
            else ()
        ),
        provider_name="ollama",
        model_name="qwen2.5:3b",
        prompt_version="meeting_translation_v1",
        schema_version=1,
        failure_code=(
            "translation_provider_failed"
            if status is TranslationArtifactStatus.FAILED
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


def test_round_trip_preserves_immutable_artifact_and_ordered_json_segments() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingTranslationRepository(session)
    artifact = _artifact(1)

    async def exercise() -> MeetingTranslationArtifact | None:
        await repository.save(artifact)
        model = session.get(MeetingTranslationArtifactModel, str(artifact.artifact_id))
        assert model is not None
        assert model.segments == [
            {
                "transcript_id": str(artifact.segments[0].transcript_id),
                "source_text": artifact.segments[0].source_text,
                "translated_text": artifact.segments[0].translated_text,
            },
            {
                "transcript_id": str(artifact.segments[1].transcript_id),
                "source_text": artifact.segments[1].source_text,
                "translated_text": artifact.segments[1].translated_text,
            },
        ]
        return await repository.get_by_id(artifact.artifact_id)

    loaded = asyncio.run(exercise())

    assert loaded == artifact
    assert isinstance(loaded.segments, tuple) if loaded is not None else False
    assert session.in_transaction()
    session.rollback()
    session.close()
    engine.dispose()


def test_latest_version_and_completed_queries_are_deterministic() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingTranslationRepository(session)
    oldest = _artifact(1, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    latest = _artifact(2, created_at=datetime(2026, 1, 2, tzinfo=UTC))
    failed = _artifact(
        3,
        status=TranslationArtifactStatus.FAILED,
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    async def exercise() -> tuple[int | None, MeetingTranslationArtifact | None]:
        await repository.save(oldest)
        await repository.save(latest)
        await repository.save(failed)
        return (
            await repository.get_latest_version(_MEETING_ID, target_language="tr"),
            await repository.get_latest_completed(_MEETING_ID),
        )

    latest_version, latest_completed = asyncio.run(exercise())

    assert latest_version == 3
    assert latest_completed == latest
    session.rollback()
    session.close()
    engine.dispose()


def test_list_for_meeting_uses_deterministic_latest_first_ordering() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingTranslationRepository(session)
    same_time = datetime(2026, 1, 1, tzinfo=UTC)
    first = _artifact(1, created_at=same_time)
    second = _artifact(2, created_at=same_time)
    other = _artifact(4, meeting_id=_OTHER_MEETING_ID)

    async def exercise() -> tuple[MeetingTranslationArtifact, ...]:
        await repository.save(first)
        await repository.save(second)
        await repository.save(other)
        return await repository.list_for_meeting(_MEETING_ID)

    artifacts = asyncio.run(exercise())

    assert isinstance(artifacts, tuple)
    assert [artifact.artifact_id for artifact in artifacts] == [
        second.artifact_id,
        first.artifact_id,
    ]
    session.rollback()
    session.close()
    engine.dispose()


def test_database_enforces_unique_meeting_language_version() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingTranslationRepository(session)
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


def test_get_by_meeting_and_version_returns_only_the_exact_artifact() -> None:
    session, engine = _session()
    repository = SQLAlchemyMeetingTranslationRepository(session)
    first = _artifact(1, version=1)
    second = _artifact(2, version=2)

    async def exercise() -> MeetingTranslationArtifact | None:
        await repository.save(first)
        await repository.save(second)
        return await repository.get_by_meeting_and_version(
            _MEETING_ID,
            target_language="tr",
            version=2,
        )

    loaded = asyncio.run(exercise())

    assert loaded == second
    session.rollback()
    session.close()
    engine.dispose()
