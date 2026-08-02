"""Tests for application-layer recording retention updates."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingState,
    UpdateAudioRetentionCommand,
)
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import UpdateAudioRetentionUseCase
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId

from .recording_fakes import recording_record


class _Repository:
    def __init__(self, record: RecordingMetadataRecord | None) -> None:
        self.record = record
        self.saves: list[RecordingMetadataRecord] = []

    async def get_by_meeting_id(
        self, meeting_id: MeetingId
    ) -> RecordingMetadataRecord | None:
        if self.record is not None and self.record.metadata.meeting_id == meeting_id:
            return self.record
        return None

    async def save(self, record: RecordingMetadataRecord) -> None:
        self.saves.append(record)
        self.record = record


class _UnitOfWork:
    def __init__(self, repository: _Repository) -> None:
        self.recordings = repository
        self.commits = 0

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class _Factory:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.units: list[_UnitOfWork] = []

    def __call__(self) -> _UnitOfWork:
        unit = _UnitOfWork(self.repository)
        self.units.append(unit)
        return unit


def _record(
    state: RecordingState = RecordingState.COMPLETED,
) -> RecordingMetadataRecord:
    return recording_record(
        recording_id=UUID(int=1),
        meeting_id=MeetingId(UUID(int=2)),
        state=state,
    )


def _use_case(
    record: RecordingMetadataRecord | None,
    now: datetime = datetime(2026, 2, 1, tzinfo=UTC),
) -> tuple[UpdateAudioRetentionUseCase, _Repository, _Factory]:
    repository = _Repository(record)
    factory = _Factory(repository)
    return (
        UpdateAudioRetentionUseCase(
            unit_of_work_factory=factory,
            clock=lambda: now,
        ),
        repository,
        factory,
    )


def test_updates_timed_and_manual_retention_without_touching_storage_metadata() -> None:
    record = _record()
    for policy, days in (
        (RecordingRetentionPolicy.ONE_DAY, 1),
        (RecordingRetentionPolicy.SEVEN_DAYS, 7),
        (RecordingRetentionPolicy.THIRTY_DAYS, 30),
    ):
        use_case, repository, factory = _use_case(record)
        result = asyncio.run(
            use_case.execute(
                UpdateAudioRetentionCommand(
                    meeting_id=record.metadata.meeting_id,
                    retention_policy=policy,
                    protected=False,
                )
            )
        )
        assert result.expires_at == datetime(2026, 2, 1, tzinfo=UTC) + timedelta(
            days=days
        )
        assert result.deletion_status is RecordingDeletionStatus.SCHEDULED
        assert repository.record is not None
        assert (
            repository.record.storage_directory_token == record.storage_directory_token
        )
        assert repository.record.key_reference == record.key_reference
        assert factory.units[0].commits == 1

    use_case, _, _ = _use_case(record)
    manual = asyncio.run(
        use_case.execute(
            UpdateAudioRetentionCommand(
                meeting_id=record.metadata.meeting_id,
                retention_policy=RecordingRetentionPolicy.MANUAL,
                protected=False,
            )
        )
    )
    assert manual.expires_at is None
    assert manual.deletion_status is RecordingDeletionStatus.NOT_SCHEDULED


def test_protection_missing_deleted_and_clock_validation() -> None:
    record = _record()
    use_case, repository, _ = _use_case(record)
    result = asyncio.run(
        use_case.execute(
            UpdateAudioRetentionCommand(
                meeting_id=record.metadata.meeting_id,
                retention_policy=RecordingRetentionPolicy.ONE_DAY,
                protected=True,
            )
        )
    )
    assert result.deletion_status is RecordingDeletionStatus.NOT_SCHEDULED
    assert repository.record is not None
    assert repository.record.metadata.state is RecordingState.COMPLETED

    missing, _, _ = _use_case(None)
    with pytest.raises(LookupError):
        asyncio.run(
            missing.execute(
                UpdateAudioRetentionCommand(
                    meeting_id=record.metadata.meeting_id,
                    retention_policy=RecordingRetentionPolicy.ONE_DAY,
                    protected=False,
                )
            )
        )

    deleted, _, _ = _use_case(_record(RecordingState.DELETED))
    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(
            deleted.execute(
                UpdateAudioRetentionCommand(
                    meeting_id=record.metadata.meeting_id,
                    retention_policy=RecordingRetentionPolicy.ONE_DAY,
                    protected=False,
                )
            )
        )
    invalid_clock, _, _ = _use_case(record, datetime(2026, 2, 1))
    with pytest.raises(ApplicationValidationError):
        asyncio.run(
            invalid_clock.execute(
                UpdateAudioRetentionCommand(
                    meeting_id=record.metadata.meeting_id,
                    retention_policy=RecordingRetentionPolicy.ONE_DAY,
                    protected=False,
                )
            )
        )
