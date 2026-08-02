"""Tests for deterministic SQLAlchemy recording metadata queries."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.domain.value_objects import MeetingId
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.recording_repository import (
    SQLAlchemyRecordingRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _record(recording_id: int, created_at: datetime) -> RecordingMetadataRecord:
    return RecordingMetadataRecord(
        metadata=RecordingMetadata(
            recording_id=UUID(int=recording_id),
            meeting_id=MeetingId(UUID(int=100 + recording_id)),
            state=RecordingState.COMPLETED,
            retention_policy=RecordingRetentionPolicy.SEVEN_DAYS,
            created_at=created_at,
            expires_at=created_at + timedelta(days=7),
            capture_anchor_utc=None,
            duration_seconds=None,
            protected=False,
            consent_confirmed=True,
            consent_confirmed_at=created_at,
            deletion_status=RecordingDeletionStatus.SCHEDULED,
            deleted_at=None,
            encryption_format_version=1,
            container_format="m4a",
            segment_count=0,
            has_gaps=False,
        ),
        storage_directory_token="opaque-storage-token",
        key_reference="a" * 24,
        current_segment_index=0,
    )


def test_list_all_is_ordered_immutable_and_does_not_commit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    repository = SQLAlchemyRecordingRepository(session)
    later = _record(2, datetime(2026, 1, 2, tzinfo=UTC))
    earlier = _record(1, datetime(2026, 1, 1, tzinfo=UTC))

    async def exercise() -> tuple[RecordingMetadataRecord, ...]:
        await repository.save(later)
        await repository.save(earlier)
        return await repository.list_all()

    records = asyncio.run(exercise())

    assert isinstance(records, tuple)
    assert [record.metadata.recording_id for record in records] == [
        earlier.metadata.recording_id,
        later.metadata.recording_id,
    ]
    assert session.in_transaction()
    session.rollback()
    session.close()
    engine.dispose()
