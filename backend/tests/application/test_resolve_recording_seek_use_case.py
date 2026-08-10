"""Tests for durable V1 recording seek resolution."""

from dataclasses import replace
from math import inf, nan
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingMediaFormat,
    RecordingSegmentTiming,
    RecordingState,
    ResolveRecordingSeekCommand,
)
from app.application.exceptions import (
    ApplicationValidationError,
    RecordingPlaybackUnavailableError,
)
from app.application.use_cases import ResolveRecordingSeekUseCase
from app.domain.value_objects import MeetingId

from .recording_fakes import FakeUnitOfWork, FakeUnitOfWorkFactory, recording_record


def _record(
    *,
    state: RecordingState = RecordingState.COMPLETED,
    has_gaps: bool = False,
):
    record = recording_record(
        recording_id=UUID(int=1),
        meeting_id=MeetingId(UUID(int=2)),
        state=state,
    )
    return replace(
        record,
        metadata=replace(
            record.metadata,
            container_format=RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1,
            has_gaps=has_gaps,
        ),
    )


def _timing(index: int, count: int, start: int) -> RecordingSegmentTiming:
    return RecordingSegmentTiming(
        recording_id=UUID(int=1),
        segment_index=index,
        sample_count=count,
        start_sample=start,
    )


async def _resolve(
    unit_of_work: FakeUnitOfWork,
    target_seconds: float,
):
    return await ResolveRecordingSeekUseCase(
        FakeUnitOfWorkFactory(unit_of_work)
    ).execute(
        ResolveRecordingSeekCommand(
            meeting_id=MeetingId(UUID(int=2)),
            target_seconds=target_seconds,
        )
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target_seconds", "expected_sample", "expected_offset"),
    [(0.0, 0, 0), (2.5, 40_000, 40_000)],
)
async def test_resolves_positions_inside_the_first_segment(
    target_seconds: float,
    expected_sample: int,
    expected_offset: int,
) -> None:
    unit_of_work = FakeUnitOfWork(
        records=[_record()],
        timings=[_timing(0, 80_000, 0)],
    )

    resolution = await _resolve(unit_of_work, target_seconds)

    assert resolution.at_end is False
    assert resolution.target is not None
    assert resolution.target.segment_index == 0
    assert resolution.target.target_sample == expected_sample
    assert resolution.target.offset_samples == expected_offset


@pytest.mark.anyio
async def test_exact_segment_boundary_resolves_to_the_next_segment() -> None:
    unit_of_work = FakeUnitOfWork(
        records=[_record()],
        timings=[_timing(0, 80_000, 0), _timing(1, 80_000, 80_000)],
    )

    resolution = await _resolve(unit_of_work, 5.0)

    assert resolution.target is not None
    assert resolution.target.segment_index == 1
    assert resolution.target.offset_samples == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target_seconds", "segment_index", "offset_samples"),
    [(5.0, 1, 0), (6.9, 1, 30_400), (7.0, 2, 0), (7.1, 2, 1_600)],
)
async def test_short_middle_segment_uses_persisted_sample_offsets(
    target_seconds: float,
    segment_index: int,
    offset_samples: int,
) -> None:
    unit_of_work = FakeUnitOfWork(
        records=[_record()],
        timings=[
            _timing(0, 80_000, 0),
            _timing(1, 32_000, 80_000),
            _timing(2, 80_000, 112_000),
        ],
    )

    resolution = await _resolve(unit_of_work, target_seconds)

    assert resolution.target is not None
    assert resolution.target.segment_index == segment_index
    assert resolution.target.offset_samples == offset_samples


@pytest.mark.anyio
async def test_near_end_resolves_to_final_segment_and_exact_end_is_terminal() -> None:
    unit_of_work = FakeUnitOfWork(
        records=[_record()],
        timings=[_timing(0, 80_000, 0), _timing(1, 80_000, 80_000)],
    )

    near_end = await _resolve(unit_of_work, 9.9999)
    at_end = await _resolve(unit_of_work, 10.0)

    assert near_end.target is not None
    assert near_end.target.segment_index == 1
    assert near_end.target.offset_samples == 79_998
    assert at_end.at_end is True
    assert at_end.target is None


@pytest.mark.anyio
@pytest.mark.parametrize("target_seconds", [-0.1, nan, inf, -inf])
async def test_rejects_invalid_target_seconds_before_lookup(
    target_seconds: float,
) -> None:
    with pytest.raises(ApplicationValidationError):
        ResolveRecordingSeekCommand(
            meeting_id=MeetingId(UUID(int=2)),
            target_seconds=target_seconds,
        )


@pytest.mark.anyio
async def test_rejects_target_beyond_authoritative_duration() -> None:
    unit_of_work = FakeUnitOfWork(
        records=[_record()],
        timings=[_timing(0, 80_000, 0)],
    )

    with pytest.raises(ApplicationValidationError):
        await _resolve(unit_of_work, 5.0001)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "record",
    [
        None,
        _record(state=RecordingState.RECORDING),
        _record(state=RecordingState.DELETED),
        recording_record(
            recording_id=UUID(int=1),
            meeting_id=MeetingId(UUID(int=2)),
            state=RecordingState.COMPLETED,
        ),
    ],
)
async def test_rejects_unavailable_recordings(record: object | None) -> None:
    records = [] if record is None else [record]
    unit_of_work = FakeUnitOfWork(records=records)

    with pytest.raises(RecordingPlaybackUnavailableError):
        await _resolve(unit_of_work, 0.0)


@pytest.mark.anyio
async def test_rejects_missing_or_inconsistent_v1_timing() -> None:
    record = _record()
    for timings in ([], [_timing(1, 80_000, 0)]):
        unit_of_work = FakeUnitOfWork(records=[record], timings=timings)

        with pytest.raises(ApplicationValidationError) as error:
            await _resolve(unit_of_work, 0.0)

        assert "opaque" not in str(error.value)
        assert unit_of_work.commits == 0


@pytest.mark.anyio
async def test_seek_allows_capture_gaps_on_the_playback_timeline() -> None:
    unit_of_work = FakeUnitOfWork(
        records=[_record(has_gaps=True)],
        timings=[_timing(0, 80_000, 0)],
    )

    resolution = await _resolve(unit_of_work, 2.0)

    assert resolution.target is not None
    assert resolution.target.offset_samples == 32_000


@pytest.mark.anyio
async def test_long_timing_index_uses_segment_samples_not_fixed_duration() -> None:
    timings = [
        _timing(index, 32_000 if index == 1_000 else 80_000, 0)
        for index in range(2_001)
    ]
    start_sample = 0
    corrected_timings: list[RecordingSegmentTiming] = []
    for timing in timings:
        corrected_timings.append(
            _timing(timing.segment_index, timing.sample_count, start_sample)
        )
        start_sample += timing.sample_count
    unit_of_work = FakeUnitOfWork(records=[_record()], timings=corrected_timings)

    resolution = await _resolve(unit_of_work, 5_000.1)

    assert resolution.target is not None
    assert resolution.target.segment_index == 1_000
    assert resolution.target.offset_samples == 1_600
