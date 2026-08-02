"""Tests for immediate local Meeting-audio deletion."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    DeleteMeetingAudioCommand,
    DeleteMeetingAudioResult,
    RecordingDeletionStatus,
    RecordingEncryptionKey,
    RecordingKeyReference,
    RecordingMetadataRecord,
    RecordingSegmentDescriptor,
    RecordingState,
)
from app.application.exceptions import (
    RecordingKeyNotFoundError,
    RecordingKeyStoreError,
    RecordingSegmentNotFoundError,
    RecordingStorageError,
)
from app.application.use_cases import DeleteMeetingAudioUseCase
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

    async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None:
        if (
            self.record is not None
            and self.record.metadata.recording_id == recording_id
        ):
            return self.record
        return None

    async def save(self, record: RecordingMetadataRecord) -> None:
        self.saves.append(record)
        self.record = record


class _UnitOfWork:
    def __init__(self, repository: _Repository, active: list[int]) -> None:
        self.recordings = repository
        self._active = active
        self.commits = 0

    async def __aenter__(self) -> "_UnitOfWork":
        self._active[0] += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        self._active[0] -= 1

    async def commit(self) -> None:
        self.commits += 1


class _Factory:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.active = [0]
        self.units: list[_UnitOfWork] = []

    def __call__(self) -> _UnitOfWork:
        unit = _UnitOfWork(self.repository, self.active)
        self.units.append(unit)
        return unit


class _Storage:
    def __init__(self, active: list[int]) -> None:
        self.active = active
        self.deleted: list[UUID] = []
        self.error: BaseException | None = None

    async def delete_recording(self, recording_id: UUID) -> None:
        assert self.active[0] == 0
        self.deleted.append(recording_id)
        if self.error is not None:
            raise self.error

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
        return False


class _KeyStore:
    def __init__(self, active: list[int]) -> None:
        self.active = active
        self.deleted: list[RecordingKeyReference] = []
        self.error: BaseException | None = None

    async def delete_key(self, reference: RecordingKeyReference) -> None:
        assert self.active[0] == 0
        self.deleted.append(reference)
        if self.error is not None:
            raise self.error

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference:
        raise NotImplementedError

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        raise NotImplementedError

    async def exists(self, reference: RecordingKeyReference) -> bool:
        return False


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
) -> tuple[
    DeleteMeetingAudioUseCase,
    _Repository,
    _Factory,
    _Storage,
    _KeyStore,
]:
    repository = _Repository(record)
    factory = _Factory(repository)
    storage = _Storage(factory.active)
    key_store = _KeyStore(factory.active)

    def clock() -> datetime:
        return datetime(2026, 2, 1, tzinfo=UTC)

    return (
        DeleteMeetingAudioUseCase(
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


def test_missing_and_deleted_recordings_are_idempotently_absent() -> None:
    meeting_id = MeetingId(UUID(int=2))
    missing, _, missing_factory, _, _ = _use_case(None)
    result = asyncio.run(
        missing.execute(DeleteMeetingAudioCommand(meeting_id=meeting_id))
    )
    assert result.already_absent is True
    assert missing_factory.units[0].commits == 0

    deleted, _, deleted_factory, storage, _ = _use_case(_record(RecordingState.DELETED))
    result = asyncio.run(
        deleted.execute(DeleteMeetingAudioCommand(meeting_id=meeting_id))
    )
    assert result.already_absent is True
    assert storage.deleted == []
    assert deleted_factory.units[0].commits == 0


def test_completed_and_pending_recordings_delete_in_isolated_phases() -> None:
    for state in (RecordingState.COMPLETED, RecordingState.PENDING):
        record = _record(state)
        use_case, repository, factory, storage, key_store = _use_case(record)
        result = asyncio.run(
            use_case.execute(
                DeleteMeetingAudioCommand(meeting_id=record.metadata.meeting_id)
            )
        )
        assert result.deleted is True
        assert storage.deleted == [record.metadata.recording_id]
        assert len(key_store.deleted) == 1
        assert (
            repository.saves[0].metadata.deletion_status
            is RecordingDeletionStatus.DELETING
        )
        assert repository.record is not None
        assert repository.record.metadata.state is RecordingState.DELETED
        assert repository.record.metadata.deleted_at == datetime(2026, 2, 1, tzinfo=UTC)
        assert [unit.commits for unit in factory.units] == [0, 1, 1]


def test_active_recordings_are_rejected_without_external_calls() -> None:
    for state in (RecordingState.RECORDING, RecordingState.FINALIZING):
        record = _record(state)
        use_case, _, _, storage, key_store = _use_case(record)
        with pytest.raises(InvalidStateTransitionError):
            asyncio.run(
                use_case.execute(
                    DeleteMeetingAudioCommand(meeting_id=record.metadata.meeting_id)
                )
            )
        assert storage.deleted == []
        assert key_store.deleted == []


def test_missing_resources_are_idempotent_but_failures_remain_retryable() -> None:
    record = _record()
    use_case, repository, _, storage, key_store = _use_case(record)
    storage.error = RecordingSegmentNotFoundError()
    key_store.error = RecordingKeyNotFoundError()
    result = asyncio.run(
        use_case.execute(
            DeleteMeetingAudioCommand(meeting_id=record.metadata.meeting_id)
        )
    )
    assert result.deleted is True
    assert repository.record is not None
    assert repository.record.metadata.state is RecordingState.DELETED

    failing, failed_repository, _, failed_storage, failed_key_store = _use_case(
        _record()
    )
    failed_storage.error = RuntimeError("private path")
    with pytest.raises(RecordingStorageError) as error:
        asyncio.run(
            failing.execute(
                DeleteMeetingAudioCommand(meeting_id=record.metadata.meeting_id)
            )
        )
    assert "private path" not in str(error.value)
    assert failed_key_store.deleted == []
    assert failed_repository.record is not None
    assert failed_repository.record.failure_code == "storage_delete_failed"

    key_failing, key_repository, _, _, key_store = _use_case(_record())
    key_store.error = RuntimeError("private key")
    with pytest.raises(RecordingKeyStoreError):
        asyncio.run(
            key_failing.execute(
                DeleteMeetingAudioCommand(meeting_id=record.metadata.meeting_id)
            )
        )
    assert key_repository.record is not None
    assert key_repository.record.failure_code == "key_delete_failed"


def test_deleting_and_failed_records_retry_and_result_is_immutable() -> None:
    for status, failure_code in (
        (RecordingDeletionStatus.DELETING, None),
        (RecordingDeletionStatus.FAILED, "storage_delete_failed"),
    ):
        record = _record()
        use_case, repository, _, _, _ = _use_case(record)
        repository.record = replace(
            record,
            metadata=replace(record.metadata, deletion_status=status),
            failure_code=failure_code,
        )
        result = asyncio.run(
            use_case.execute(
                DeleteMeetingAudioCommand(meeting_id=record.metadata.meeting_id)
            )
        )
        assert result.deleted is True

    result = DeleteMeetingAudioResult(
        meeting_id=MeetingId(UUID(int=2)),
        recording_id=UUID(int=1),
        deleted=True,
        already_absent=False,
    )
    with pytest.raises(FrozenInstanceError):
        result.deleted = False
