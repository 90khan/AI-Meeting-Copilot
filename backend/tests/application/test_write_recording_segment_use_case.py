"""Tests for sequential encrypted recording-segment persistence."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingMediaFormat,
    RecordingSegmentDescriptor,
    RecordingSegmentTiming,
    RecordingState,
    WriteRecordingSegmentCommand,
)
from app.application.exceptions import RecordingSegmentLifecycleError
from app.application.use_cases import WriteRecordingSegmentUseCase
from app.domain.exceptions import InvalidStateTransitionError
from app.infrastructure.audio import WavChunkBuilder

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


class _Timings:
    def __init__(
        self,
        timings: tuple[RecordingSegmentTiming, ...] = (),
        *,
        events: list[str] | None = None,
        fail_on_save: bool = False,
    ) -> None:
        self.timings = list(timings)
        self.events = events if events is not None else []
        self.fail_on_save = fail_on_save
        self.save_calls = 0

    async def get_last(self, recording_id: UUID) -> RecordingSegmentTiming | None:
        matching = [item for item in self.timings if item.recording_id == recording_id]
        if not matching:
            return None
        return max(matching, key=lambda item: item.segment_index)

    async def save(self, timing: RecordingSegmentTiming) -> None:
        self.save_calls += 1
        self.events.append("timing.save")
        if self.fail_on_save:
            raise RuntimeError("persistence failed")
        self.timings = [
            item
            for item in self.timings
            if (item.recording_id, item.segment_index)
            != (timing.recording_id, timing.segment_index)
        ]
        self.timings.append(timing)


class _UnitOfWork:
    def __init__(self, recordings: _Recordings, timings: _Timings) -> None:
        self.recordings = recordings
        self.recording_segment_timings = timings
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
        self.recording_segment_timings.events.append("commit")


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
        self._storage.events.append("storage.write")
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
        self.events = unit_of_work.recording_segment_timings.events

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

    async def delete_segment(self, recording_id: UUID, segment_index: int) -> None:
        assert not self.unit_of_work.active
        self.writes = [item for item in self.writes if item[0] != segment_index]
        self.events.append("storage.delete")


def _wav(frame_count: int) -> bytes:
    return WavChunkBuilder().build(b"\x00\x00" * frame_count).data


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
    timings = _Timings()
    unit_of_work = _UnitOfWork(recordings, timings)
    storage = _Storage(unit_of_work)
    use_case = WriteRecordingSegmentUseCase(lambda: unit_of_work, storage)
    first_wav, second_wav = _wav(3), _wav(2)

    first = await use_case.execute(
        WriteRecordingSegmentCommand(
            recording_id=UUID(int=1), segment_index=0, wav_bytes=first_wav
        )
    )
    second = await use_case.execute(
        WriteRecordingSegmentCommand(
            recording_id=UUID(int=1), segment_index=1, wav_bytes=second_wav
        )
    )

    assert first.segment_index == 0
    assert second.segment_index == 1
    assert storage.writes == [(0, first_wav), (1, second_wav)]
    assert recordings.record.current_segment_index == 2
    assert recordings.save_calls == unit_of_work.commits == 2
    assert unit_of_work.exits == 4
    assert timings.timings == [
        RecordingSegmentTiming(
            recording_id=UUID(int=1),
            segment_index=0,
            sample_count=3,
            start_sample=0,
        ),
        RecordingSegmentTiming(
            recording_id=UUID(int=1),
            segment_index=1,
            sample_count=2,
            start_sample=3,
        ),
    ]
    assert timings.events == [
        "storage.write",
        "timing.save",
        "commit",
        "storage.write",
        "timing.save",
        "commit",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("index", [0, 2])
async def test_rejects_duplicate_backward_and_skipped_indexes(index: int) -> None:
    record = replace(_record(), current_segment_index=1)
    recordings = _Recordings(record)
    timings = _Timings(
        (
            RecordingSegmentTiming(
                recording_id=UUID(int=1),
                segment_index=0,
                sample_count=3,
                start_sample=0,
            ),
        )
    )
    unit_of_work = _UnitOfWork(recordings, timings)
    storage = _Storage(unit_of_work)
    storage.writes.append((0, _wav(3)))
    use_case = WriteRecordingSegmentUseCase(lambda: unit_of_work, storage)

    with pytest.raises(RecordingSegmentLifecycleError):
        await use_case.execute(
            WriteRecordingSegmentCommand(
                recording_id=UUID(int=1), segment_index=index, wav_bytes=_wav(1)
            )
        )

    assert storage.writes == [(0, _wav(3))]
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
        unit_of_work = _UnitOfWork(recordings, _Timings())
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
                    recording_id=UUID(int=1), segment_index=0, wav_bytes=_wav(1)
                )
            )
        assert not storage.writes


@pytest.mark.anyio
async def test_derives_sample_count_from_wav_and_commits_after_storage_write() -> None:
    events: list[str] = []
    recordings = _Recordings(_record())
    timings = _Timings(events=events)
    unit_of_work = _UnitOfWork(recordings, timings)
    storage = _Storage(unit_of_work)
    use_case = WriteRecordingSegmentUseCase(lambda: unit_of_work, storage)

    await use_case.execute(
        WriteRecordingSegmentCommand(
            recording_id=UUID(int=1), segment_index=0, wav_bytes=_wav(7)
        )
    )

    assert timings.timings == [
        RecordingSegmentTiming(
            recording_id=UUID(int=1),
            segment_index=0,
            sample_count=7,
            start_sample=0,
        )
    ]
    assert events == ["storage.write", "timing.save", "commit"]
    assert len(storage.writes) == 1


@pytest.mark.anyio
async def test_timing_persistence_failure_compensates_the_completed_segment() -> None:
    recordings = _Recordings(_record())
    timings = _Timings(fail_on_save=True)
    unit_of_work = _UnitOfWork(recordings, timings)
    storage = _Storage(unit_of_work)
    use_case = WriteRecordingSegmentUseCase(lambda: unit_of_work, storage)

    with pytest.raises(RuntimeError, match="persistence failed"):
        await use_case.execute(
            WriteRecordingSegmentCommand(
                recording_id=UUID(int=1), segment_index=0, wav_bytes=_wav(1)
            )
        )

    assert storage.writes == []
    assert timings.timings == []
    assert unit_of_work.commits == 0
    assert timings.events == ["storage.write", "timing.save", "storage.delete"]
