"""Tests for one-shot recording retention cleanup."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingEncryptionKey,
    RecordingKeyReference,
    RecordingMetadataRecord,
    RecordingSegmentDescriptor,
    RecordingState,
)
from app.application.exceptions import (
    ApplicationValidationError,
    RecordingKeyNotFoundError,
    RecordingSegmentNotFoundError,
)
from app.application.services import RecordingRetentionCleanupService
from app.domain.value_objects import MeetingId

from ..recording_fakes import recording_record


class _Repository:
    def __init__(self, records: list[RecordingMetadataRecord]) -> None:
        self.records = {record.metadata.recording_id: record for record in records}
        self.expired = tuple(records)
        self.saves: list[RecordingMetadataRecord] = []

    async def list_expired(
        self, *, as_of: datetime
    ) -> tuple[RecordingMetadataRecord, ...]:
        return self.expired

    async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None:
        return self.records.get(recording_id)

    async def save(self, record: RecordingMetadataRecord) -> None:
        self.saves.append(record)
        self.records[record.metadata.recording_id] = record


class _UnitOfWork:
    def __init__(self, repository: _Repository, active: list[int]) -> None:
        self.recordings = repository
        self._active = active
        self.commits = 0
        self.exited = False

    async def __aenter__(self) -> "_UnitOfWork":
        self._active[0] += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        self._active[0] -= 1
        self.exited = True

    async def commit(self) -> None:
        self.commits += 1


class _Factory:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.active = [0]
        self.created: list[_UnitOfWork] = []

    def __call__(self) -> _UnitOfWork:
        unit_of_work = _UnitOfWork(self.repository, self.active)
        self.created.append(unit_of_work)
        return unit_of_work


class _Storage:
    def __init__(self, active: list[int]) -> None:
        self.active = active
        self.deleted: list[UUID] = []
        self.errors: dict[UUID, BaseException] = {}

    async def delete_recording(self, recording_id: UUID) -> None:
        assert self.active[0] == 0
        self.deleted.append(recording_id)
        error = self.errors.get(recording_id)
        if error is not None:
            raise error

    async def create_segment_writer(
        self,
        recording_id: UUID,
        segment_index: int,
        key_reference: RecordingKeyReference,
    ) -> None:
        raise NotImplementedError

    async def list_segments(
        self, recording_id: UUID
    ) -> tuple[RecordingSegmentDescriptor, ...]:
        raise NotImplementedError

    async def recording_exists(self, recording_id: UUID) -> bool:
        return recording_id in self.deleted


class _KeyStore:
    def __init__(self, active: list[int]) -> None:
        self.active = active
        self.deleted: list[RecordingKeyReference] = []
        self.errors: dict[str, BaseException] = {}

    async def delete_key(self, reference: RecordingKeyReference) -> None:
        assert self.active[0] == 0
        self.deleted.append(reference)
        error = self.errors.get(reference.value)
        if error is not None:
            raise error

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference:
        raise NotImplementedError

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        raise NotImplementedError

    async def exists(self, reference: RecordingKeyReference) -> bool:
        return reference in self.deleted


def _record(recording_id: int, state: RecordingState = RecordingState.COMPLETED):
    return recording_record(
        recording_id=UUID(int=recording_id),
        meeting_id=MeetingId(UUID(int=100 + recording_id)),
        state=state,
    )


def _service(
    records: list[RecordingMetadataRecord],
) -> tuple[
    RecordingRetentionCleanupService,
    _Repository,
    _Factory,
    _Storage,
    _KeyStore,
]:
    repository = _Repository(records)
    factory = _Factory(repository)
    storage = _Storage(factory.active)
    key_store = _KeyStore(factory.active)

    def clock() -> datetime:
        return datetime(2026, 1, 9, tzinfo=UTC)

    return (
        RecordingRetentionCleanupService(
            unit_of_work_factory=factory,
            recording_storage=storage,
            recording_key_store=key_store,
            clock=clock,
        ),
        repository,
        factory,
        storage,
        key_store,
    )


def test_no_expired_recordings_returns_empty_result() -> None:
    service, _, factory, _, _ = _service([])

    result = asyncio.run(service.run_once())

    assert result.checked_count == 0
    assert len(factory.created) == 1
    assert factory.created[0].commits == 0


def test_deletes_records_in_order_with_separate_metadata_transitions() -> None:
    first, second = _record(1), _record(2)
    service, repository, factory, storage, key_store = _service([first, second])

    result = asyncio.run(service.run_once())

    assert [item.recording_id for item in result.items] == [
        first.metadata.recording_id,
        second.metadata.recording_id,
    ]
    assert result.deleted_count == 2
    assert storage.deleted == [
        first.metadata.recording_id,
        second.metadata.recording_id,
    ]
    assert len(key_store.deleted) == 2
    assert all(unit_of_work.commits == 1 for unit_of_work in factory.created[1:])
    assert (
        repository.saves[0].metadata.deletion_status is RecordingDeletionStatus.DELETING
    )
    assert repository.saves[1].metadata.state is RecordingState.DELETED
    assert repository.records[first.metadata.recording_id].metadata.expires_at is None


def test_missing_storage_still_deletes_key_and_marks_metadata_deleted() -> None:
    record = _record(1)
    service, repository, _, storage, key_store = _service([record])
    storage.errors[record.metadata.recording_id] = RecordingSegmentNotFoundError()

    result = asyncio.run(service.run_once())

    assert result.missing_count == 1
    assert len(key_store.deleted) == 1
    assert (
        repository.records[record.metadata.recording_id].metadata.state
        is RecordingState.DELETED
    )


def test_missing_key_is_idempotent_and_failures_do_not_block_later_records() -> None:
    first, second = _record(1), _record(2)
    service, repository, _, storage, key_store = _service([first, second])
    storage.errors[first.metadata.recording_id] = RuntimeError("private path")
    key_store.errors[second.key_reference] = RecordingKeyNotFoundError()

    result = asyncio.run(service.run_once())

    assert [item.outcome.value for item in result.items] == ["failed", "deleted"]
    assert repository.records[first.metadata.recording_id].failure_code == (
        "storage_delete_failed"
    )
    assert (
        repository.records[second.metadata.recording_id].metadata.state
        is RecordingState.DELETED
    )


def test_key_failure_is_persisted_and_missing_metadata_is_missing() -> None:
    record = _record(1)
    service, repository, _, _, key_store = _service([record])
    key_store.errors[record.key_reference] = RuntimeError("secret")

    result = asyncio.run(service.run_once())

    assert result.failed_count == 1
    assert (
        repository.records[record.metadata.recording_id].failure_code
        == "key_delete_failed"
    )

    missing_service, missing_repository, _, _, _ = _service([record])
    missing_repository.records.clear()
    missing_result = asyncio.run(missing_service.run_once())
    assert missing_result.missing_count == 1


def test_retry_and_cancellation_behavior() -> None:
    record = _record(1)
    service, repository, _, _, _ = _service([record])
    repository.records[record.metadata.recording_id] = recording_record(
        recording_id=record.metadata.recording_id,
        meeting_id=record.metadata.meeting_id,
        state=RecordingState.COMPLETED,
        failure_code="storage_delete_failed",
    )
    result = asyncio.run(service.run_once())
    assert result.deleted_count == 1
    assert (
        repository.records[record.metadata.recording_id].metadata.state
        is RecordingState.DELETED
    )

    cancelling_record = _record(2)
    cancelling_service, _, _, cancelling_storage, _ = _service([cancelling_record])
    cancelling_storage.errors[cancelling_record.metadata.recording_id] = (
        asyncio.CancelledError()
    )
    try:
        asyncio.run(cancelling_service.run_once())
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("Cancellation must propagate.")


def test_cleanup_clock_requires_utc() -> None:
    record = _record(1)
    repository = _Repository([record])
    factory = _Factory(repository)
    service = RecordingRetentionCleanupService(
        unit_of_work_factory=factory,
        recording_storage=_Storage(factory.active),
        recording_key_store=_KeyStore(factory.active),
        clock=lambda: datetime(2026, 1, 1),
    )
    with pytest.raises(ApplicationValidationError):
        asyncio.run(service.run_once())
