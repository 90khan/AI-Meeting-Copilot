"""Tests for finalizing local recording metadata."""

from dataclasses import replace
from math import nan
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    FinalizeRecordingCommand,
    RecordingMediaFormat,
    RecordingSegmentTiming,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import FinalizeRecordingUseCase
from app.domain.entities import Meeting
from app.domain.exceptions import InvalidStateTransitionError

from .recording_fakes import FakeUnitOfWork, FakeUnitOfWorkFactory, recording_record


@pytest.mark.anyio
@pytest.mark.parametrize("state", [RecordingState.RECORDING, RecordingState.FINALIZING])
async def test_finalize_moves_valid_recording_to_completed_without_touching_meeting(
    state: RecordingState,
) -> None:
    meeting = Meeting.create(name="Review")
    record = recording_record(
        recording_id=UUID(int=1), meeting_id=meeting.id, state=state
    )
    unit_of_work = FakeUnitOfWork([meeting], [record])

    await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
        FinalizeRecordingCommand(
            recording_id=UUID(int=1),
            duration_seconds=12.5,
            segment_count=3,
            has_gaps=True,
        )
    )

    saved = unit_of_work.recordings.records[UUID(int=1)]
    assert saved.metadata.state is RecordingState.COMPLETED
    assert saved.metadata.duration_seconds == 12.5
    assert saved.metadata.segment_count == 3
    assert saved.metadata.has_gaps is True
    assert unit_of_work.meetings.meetings[meeting.id] is meeting
    assert meeting.transcripts == ()
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1
    assert unit_of_work.recording_segment_timings.list_calls == 0


@pytest.mark.anyio
async def test_finalize_v1_derives_one_full_segment_from_persisted_timing() -> None:
    record = _v1_record()
    unit_of_work = FakeUnitOfWork(
        records=[record],
        timings=[_timing(0, 80_000, 0)],
    )

    await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
        FinalizeRecordingCommand(
            recording_id=record.metadata.recording_id,
            duration_seconds=5.0,
            segment_count=1,
            has_gaps=False,
        )
    )

    saved = unit_of_work.recordings.records[record.metadata.recording_id]
    assert saved.metadata.state is RecordingState.COMPLETED
    assert saved.metadata.segment_count == 1
    assert saved.metadata.duration_seconds == 5.0
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1


@pytest.mark.anyio
async def test_finalize_v1_derives_duration_from_multiple_full_segments() -> None:
    record = _v1_record()
    unit_of_work = FakeUnitOfWork(
        records=[record],
        timings=[_timing(0, 80_000, 0), _timing(1, 80_000, 80_000)],
    )

    await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
        FinalizeRecordingCommand(
            recording_id=record.metadata.recording_id,
            duration_seconds=10.0,
            segment_count=2,
            has_gaps=False,
        )
    )

    saved = unit_of_work.recordings.records[record.metadata.recording_id]
    assert saved.metadata.segment_count == 2
    assert saved.metadata.duration_seconds == 10.0


@pytest.mark.anyio
async def test_finalize_v1_uses_variable_sample_counts_and_preserves_gaps() -> None:
    record = _v1_record()
    unit_of_work = FakeUnitOfWork(
        records=[record],
        timings=[
            _timing(0, 80_000, 0),
            _timing(1, 32_000, 80_000),
            _timing(2, 80_000, 112_000),
        ],
    )

    await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
        FinalizeRecordingCommand(
            recording_id=record.metadata.recording_id,
            duration_seconds=12.0,
            segment_count=3,
            has_gaps=True,
        )
    )

    saved = unit_of_work.recordings.records[record.metadata.recording_id]
    assert saved.metadata.segment_count == 3
    assert saved.metadata.duration_seconds == 12.0
    assert saved.metadata.has_gaps is True


@pytest.mark.anyio
async def test_finalize_v1_rejects_contradictory_caller_count_or_duration() -> None:
    record = _v1_record()
    for duration_seconds, segment_count in ((5.0, 2), (5.1, 1)):
        unit_of_work = FakeUnitOfWork(
            records=[record],
            timings=[_timing(0, 80_000, 0)],
        )

        with pytest.raises(ApplicationValidationError):
            await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
                FinalizeRecordingCommand(
                    recording_id=record.metadata.recording_id,
                    duration_seconds=duration_seconds,
                    segment_count=segment_count,
                    has_gaps=False,
                )
            )

        saved = unit_of_work.recordings.records[record.metadata.recording_id]
        assert saved.metadata.state is RecordingState.RECORDING
        assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0


@pytest.mark.anyio
async def test_finalize_v1_accepts_tiny_duration_representation_difference() -> None:
    record = _v1_record()
    unit_of_work = FakeUnitOfWork(
        records=[record],
        timings=[_timing(0, 80_000, 0)],
    )

    await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
        FinalizeRecordingCommand(
            recording_id=record.metadata.recording_id,
            duration_seconds=5.0000005,
            segment_count=1,
            has_gaps=False,
        )
    )

    saved = unit_of_work.recordings.records[record.metadata.recording_id]
    assert saved.metadata.duration_seconds == 5.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "timing_values",
    [
        [(1, 80_000, 0)],
        [(0, 80_000, 1)],
        [(0, 80_000, 0), (2, 80_000, 80_000)],
        [(0, 80_000, 0), (1, 80_000, 80_001)],
    ],
)
async def test_finalize_v1_rejects_inconsistent_persisted_timing(
    timing_values: list[tuple[int, int, int]],
) -> None:
    record = _v1_record()
    timings = [_timing(*values) for values in timing_values]
    unit_of_work = FakeUnitOfWork(records=[record], timings=timings)

    with pytest.raises(ApplicationValidationError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
            FinalizeRecordingCommand(
                recording_id=record.metadata.recording_id,
                duration_seconds=10.0,
                segment_count=len(timings),
                has_gaps=False,
            )
        )

    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0


@pytest.mark.anyio
async def test_finalize_v1_rejects_zero_persisted_segments() -> None:
    record = _v1_record()
    unit_of_work = FakeUnitOfWork(records=[record])

    with pytest.raises(ApplicationValidationError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
            FinalizeRecordingCommand(
                recording_id=record.metadata.recording_id,
                duration_seconds=0.0,
                segment_count=0,
                has_gaps=False,
            )
        )

    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0


@pytest.mark.anyio
async def test_finalize_rejects_missing_invalid_and_malformed_metadata() -> None:
    command = FinalizeRecordingCommand(
        recording_id=UUID(int=1), duration_seconds=1.0, segment_count=1, has_gaps=False
    )
    missing = FakeUnitOfWork()
    with pytest.raises(LookupError, match="Recording not found"):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(missing)).execute(command)

    invalid = FakeUnitOfWork(
        records=[recording_record(recording_id=UUID(int=1), meeting_id=_meeting().id)]
    )
    with pytest.raises(InvalidStateTransitionError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(invalid)).execute(command)

    record = recording_record(
        recording_id=UUID(int=1),
        meeting_id=_meeting().id,
        state=RecordingState.RECORDING,
    )
    malformed = FakeUnitOfWork(records=[record])
    with pytest.raises(ApplicationValidationError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(malformed)).execute(
            replace(command, duration_seconds=nan)
        )
    with pytest.raises(ApplicationValidationError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(malformed)).execute(
            replace(command, segment_count=-1)
        )

    for failed_unit_of_work in (missing, invalid, malformed):
        assert failed_unit_of_work.recordings.save_calls == 0
        assert failed_unit_of_work.commits == 0
        assert failed_unit_of_work.exited


def _meeting() -> Meeting:
    return Meeting.create(name="Review")


def _v1_record(
    state: RecordingState = RecordingState.RECORDING,
):
    record = recording_record(
        recording_id=UUID(int=1),
        meeting_id=_meeting().id,
        state=state,
    )
    return replace(
        record,
        metadata=replace(
            record.metadata,
            container_format=RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1,
        ),
    )


def _timing(
    segment_index: int,
    sample_count: int,
    start_sample: int,
) -> RecordingSegmentTiming:
    return RecordingSegmentTiming(
        recording_id=UUID(int=1),
        segment_index=segment_index,
        sample_count=sample_count,
        start_sample=start_sample,
    )
