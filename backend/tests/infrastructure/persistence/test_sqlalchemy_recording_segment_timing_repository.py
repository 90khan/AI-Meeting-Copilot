import asyncio
from uuid import UUID

from app.application.dto.recordings import RecordingSegmentTiming
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy import (
    recording_segment_timing_repository,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_timing_repository_round_trips_orders_and_does_not_commit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    repository_class = (
        recording_segment_timing_repository.SQLAlchemyRecordingSegmentTimingRepository
    )
    repository = repository_class(session)
    first, second = UUID(int=1), UUID(int=2)

    async def exercise() -> tuple[
        RecordingSegmentTiming | None,
        tuple[RecordingSegmentTiming, ...],
        RecordingSegmentTiming | None,
    ]:
        await repository.save(
            RecordingSegmentTiming(
                recording_id=first,
                segment_index=1,
                sample_count=32_000,
                start_sample=80_000,
            )
        )
        await repository.save(
            RecordingSegmentTiming(
                recording_id=first, segment_index=0, sample_count=80_000, start_sample=0
            )
        )
        await repository.save(
            RecordingSegmentTiming(
                recording_id=second, segment_index=0, sample_count=1, start_sample=0
            )
        )
        return (
            await repository.get(first, 9),
            await repository.list_for_recording(first),
            await repository.get_last(first),
        )

    missing, timings, last = asyncio.run(exercise())
    assert missing is None
    assert isinstance(timings, tuple)
    assert [
        (item.segment_index, item.sample_count, item.start_sample) for item in timings
    ] == [(0, 80_000, 0), (1, 32_000, 80_000)]
    assert last == timings[-1]
    assert session.in_transaction()
    session.rollback()
    session.close()
    engine.dispose()


def test_timing_repository_saves_one_value_per_composite_identity() -> None:
    """Saving the same recording/index updates timing without creating a duplicate."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    repository = (
        recording_segment_timing_repository.SQLAlchemyRecordingSegmentTimingRepository(
            session
        )
    )
    recording_id = UUID(int=1)

    async def exercise() -> tuple[RecordingSegmentTiming, ...]:
        await repository.save(
            RecordingSegmentTiming(
                recording_id=recording_id,
                segment_index=0,
                sample_count=16_000,
                start_sample=0,
            )
        )
        await repository.save(
            RecordingSegmentTiming(
                recording_id=recording_id,
                segment_index=0,
                sample_count=32_000,
                start_sample=16_000,
            )
        )
        return await repository.list_for_recording(recording_id)

    timings = asyncio.run(exercise())

    assert timings == (
        RecordingSegmentTiming(
            recording_id=recording_id,
            segment_index=0,
            sample_count=32_000,
            start_sample=16_000,
        ),
    )
    session.rollback()
    session.close()
    engine.dispose()


def test_legacy_recording_without_timing_rows_has_an_empty_timing_tuple() -> None:
    """Legacy recordings have no inferred timing metadata or synthetic rows."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    repository = (
        recording_segment_timing_repository.SQLAlchemyRecordingSegmentTimingRepository(
            session
        )
    )

    timings = asyncio.run(repository.list_for_recording(UUID(int=99)))

    assert timings == ()
    session.close()
    engine.dispose()
