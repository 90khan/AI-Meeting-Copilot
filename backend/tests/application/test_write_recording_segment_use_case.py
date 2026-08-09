"""Tests for sequential encrypted recording-segment persistence."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingMediaFormat,
    RecordingSegmentDescriptor,
    RecordingState,
    WriteRecordingSegmentCommand,
)
from app.application.exceptions import RecordingSegmentLifecycleError
from app.application.use_cases import WriteRecordingSegmentUseCase
from app.domain.exceptions import InvalidStateTransitionError

from .recording_fakes import recording_record


class _Recordings:
    def __init__(self, record: object) -> None:
        self.record = record
        self.save_calls = 0

    async def get_by_id(self, recording_id: UUID) -> object | None:
        if self.record.metadata.recording_id == recording_id:
            return self.record
        return None

    async def save(self, record: object) -> None:
        self.save_calls += 1
        self.record = record


class _UnitOfWork:
    def __init__(self, recordings: _Recordings) -> None:
        self.recordings = recordings
        self.active = False
        self.commits = 0
        self.exits = 0

    async def __aenter__(self) -> "_UnitOfWork":
        assert not self.active
        self.active = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.active = False
        self.exits += 1

    async def commit(self) -> None:
        assert self.active
        self.commits += 1


class _StorageWriter:
    def __init__(self, storage: "_Storage", index: int) -> None:
        self._storage = storage
        self._index = index
        self._data = b""

    async def write(self, data: bytes) -> None:
        assert not self._storage.unit_of_work.active
        self._data = data

    async def finalize(self) -> RecordingSegmentDescriptor:
        assert not self._storage.unit_of_work.active
        self._storage.writes.append((self._index, self._data))
        return RecordingSegmentDescriptor(
            recording_id=UUID(int=1),
            segment_index=self._index,
            plaintext_length=len(self._data),
            ciphertext_length=len(self._data) + 16,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed=True,
            format_version=1,
        )

    async def abort(self) -> None:
        return None


class _Storage:
    def __init__(self, unit_of_work: _UnitOfWork) -> None:
        self.unit_of_work = unit_of_work
        self.writes: list[tuple[int, bytes]] = []

    async def list_segments(
        self, recording_id: UUID
    ) -> tuple[RecordingSegmentDescriptor, ...]:
        assert not self.unit_of_work.active
        return tuple(
            RecordingSegmentDescriptor(
                recording_id=recording_id,
                segment_index=index,
                plaintext_length=len(data),
                ciphertext_length=len(data) + 16,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                completed=True,
                format_version=1,
            )
            for index, data in self.writes
        )

    async def create_segment_writer(
        self,
        recording_id: UUID,
        segment_index: int,
        key_reference: RecordingKeyReference,
    ) -> _StorageWriter:
        assert recording_id == UUID(int=1)
        assert key_reference.value == "a" * 24
        assert not self.unit_of_work.active
        return _StorageWriter(self, segment_index)


def _record(state: RecordingState = RecordingState.RECORDING):
    record = recording_record(
        recording_id=UUID(int=1),
        meeting_id=UUID(int=2),
        state=state,
    )
    return replace(
        record,
        metadata=replace(
            record.metadata,
            container_format=RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1,
        ),
        key_reference="a" * 24,
    )


@pytest.mark.anyio
async def test_writes_exact_bytes_sequentially_outside_database_transactions() -> None:
    recordings = _Recordings(_record())
    unit_of_work = _UnitOfWork(recordings)
    storage = _Storage(unit_of_work)
    use_case = WriteRecordingSegmentUseCase(lambda: unit_of_work, storage)

    first = await use_case.execute(
        WriteRecordingSegmentCommand(
            recording_id=UUID(int=1), segment_index=0, wav_bytes=b"first"
        )
    )
    second = await use_case.execute(
        WriteRecordingSegmentCommand(
            recording_id=UUID(int=1), segment_index=1, wav_bytes=b"second"
        )
    )

    assert first.segment_index == 0
    assert second.segment_index == 1
    assert storage.writes == [(0, b"first"), (1, b"second")]
    assert recordings.record.current_segment_index == 2
    assert recordings.save_calls == unit_of_work.commits == 2
    assert unit_of_work.exits == 4


@pytest.mark.anyio
@pytest.mark.parametrize("index", [0, 2])
async def test_rejects_duplicate_backward_and_skipped_indexes(index: int) -> None:
    recordings = _Recordings(_record())
    unit_of_work = _UnitOfWork(recordings)
    storage = _Storage(unit_of_work)
    storage.writes.append((0, b"existing"))
    use_case = WriteRecordingSegmentUseCase(lambda: unit_of_work, storage)

    with pytest.raises(RecordingSegmentLifecycleError):
        await use_case.execute(
            WriteRecordingSegmentCommand(
                recording_id=UUID(int=1), segment_index=index, wav_bytes=b"new"
            )
        )

    assert storage.writes == [(0, b"existing")]
    assert unit_of_work.commits == 0


@pytest.mark.anyio
async def test_rejects_wrong_recording_state_or_legacy_format() -> None:
    for record in (
        _record(RecordingState.PENDING),
        recording_record(
            recording_id=UUID(int=1),
            meeting_id=UUID(int=2),
            state=RecordingState.RECORDING,
        ),
    ):
        recordings = _Recordings(record)
        unit_of_work = _UnitOfWork(recordings)
        storage = _Storage(unit_of_work)
        use_case = WriteRecordingSegmentUseCase(
            lambda unit_of_work=unit_of_work: unit_of_work,
            storage,
        )

        with pytest.raises(
            (InvalidStateTransitionError, RecordingSegmentLifecycleError)
        ):
            await use_case.execute(
                WriteRecordingSegmentCommand(
                    recording_id=UUID(int=1), segment_index=0, wav_bytes=b"new"
                )
            )
        assert not storage.writes
