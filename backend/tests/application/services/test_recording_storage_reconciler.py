"""Tests for recording metadata/resource reconciliation."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.dto.recordings import (
    RecordingEncryptionKey,
    RecordingKeyReference,
    RecordingMetadataRecord,
    RecordingSegmentDescriptor,
    RecordingState,
)
from app.application.services import RecordingStorageReconciler
from app.domain.value_objects import MeetingId

from ..recording_fakes import recording_record


class _Repository:
    def __init__(self, records: list[RecordingMetadataRecord]) -> None:
        self.records = {record.metadata.recording_id: record for record in records}
        self.saves: list[RecordingMetadataRecord] = []

    async def list_all(self) -> tuple[RecordingMetadataRecord, ...]:
        return tuple(self.records.values())

    async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None:
        return self.records.get(recording_id)

    async def save(self, record: RecordingMetadataRecord) -> None:
        self.saves.append(record)
        self.records[record.metadata.recording_id] = record


class _UnitOfWork:
    def __init__(self, repository: _Repository, active: list[int]) -> None:
        self.recordings = repository
        self.active = active
        self.commits = 0

    async def __aenter__(self) -> "_UnitOfWork":
        self.active[0] += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        self.active[0] -= 1

    async def commit(self) -> None:
        self.commits += 1


class _Factory:
    def __init__(self, repository: _Repository) -> None:
        self.active = [0]
        self.repository = repository
        self.units: list[_UnitOfWork] = []

    def __call__(self) -> _UnitOfWork:
        unit = _UnitOfWork(self.repository, self.active)
        self.units.append(unit)
        return unit


class _Storage:
    def __init__(self, active: list[int]) -> None:
        self.active = active
        self.present: set[UUID] = set()
        self.deleted: list[UUID] = []
        self.error: Exception | None = None

    async def recording_exists(self, recording_id: UUID) -> bool:
        assert self.active[0] == 0
        if self.error:
            raise self.error
        return recording_id in self.present

    async def delete_recording(self, recording_id: UUID) -> None:
        assert self.active[0] == 0
        self.deleted.append(recording_id)
        self.present.discard(recording_id)

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


class _KeyStore:
    def __init__(self, active: list[int]) -> None:
        self.active = active
        self.present: set[str] = set()
        self.deleted: list[RecordingKeyReference] = []
        self.error: Exception | None = None

    async def exists(self, reference: RecordingKeyReference) -> bool:
        assert self.active[0] == 0
        if self.error:
            raise self.error
        return reference.value in self.present

    async def delete_key(self, reference: RecordingKeyReference) -> None:
        assert self.active[0] == 0
        self.deleted.append(reference)
        self.present.discard(reference.value)

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference:
        raise NotImplementedError

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        raise NotImplementedError


def _record(recording_id: int, state: RecordingState = RecordingState.COMPLETED):
    return recording_record(
        recording_id=UUID(int=recording_id),
        meeting_id=MeetingId(UUID(int=100 + recording_id)),
        state=state,
    )


def _reconciler(records: list[RecordingMetadataRecord]):
    repository = _Repository(records)
    factory = _Factory(repository)
    storage = _Storage(factory.active)
    key_store = _KeyStore(factory.active)
    return (
        RecordingStorageReconciler(
            unit_of_work_factory=factory,
            recording_storage=storage,
            recording_key_store=key_store,
            clock=lambda: datetime(2026, 2, 1, tzinfo=UTC),
        ),
        repository,
        factory,
        storage,
        key_store,
    )


def test_reconciliation_matrix_and_order() -> None:
    healthy, storage_missing, key_missing = _record(1), _record(2), _record(3)
    key_missing = replace(key_missing, key_reference="b" * 24)
    reconciler, repository, factory, storage, key_store = _reconciler(
        [healthy, storage_missing, key_missing]
    )
    storage.present.update(
        {healthy.metadata.recording_id, key_missing.metadata.recording_id}
    )
    key_store.present.update({healthy.key_reference, storage_missing.key_reference})

    result = asyncio.run(reconciler.reconcile())

    assert [item.outcome.value for item in result.items] == [
        "skipped",
        "missing",
        "failed",
    ]
    assert (
        repository.records[storage_missing.metadata.recording_id].metadata.state
        is RecordingState.MISSING
    )
    assert (
        repository.records[key_missing.metadata.recording_id].failure_code
        == "key_missing"
    )
    assert [unit.commits for unit in factory.units] == [0, 1, 1]


def test_deleted_residual_resources_are_removed_but_clean_metadata_is_skipped() -> None:
    deleted = _record(1, RecordingState.DELETED)
    reconciler, _, _, storage, key_store = _reconciler([deleted])
    storage.present.add(deleted.metadata.recording_id)
    key_store.present.add(deleted.key_reference)

    result = asyncio.run(reconciler.reconcile())

    assert result.deleted_count == 1
    assert storage.deleted == [deleted.metadata.recording_id]
    assert len(key_store.deleted) == 1
    clean_result = asyncio.run(reconciler.reconcile())
    assert clean_result.skipped_count == 1


def test_check_failure_does_not_block_later_records() -> None:
    first, second = _record(1), _record(2)
    reconciler, _, _, storage, _ = _reconciler([first, second])
    storage.error = RuntimeError("private path")
    result = asyncio.run(reconciler.reconcile())
    assert result.failed_count == 2
    assert all(item.message == "Recording cleanup failed." for item in result.items)
