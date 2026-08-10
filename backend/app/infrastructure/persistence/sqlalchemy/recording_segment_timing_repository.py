"""SQLAlchemy persistence for recording playback sample offsets."""

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.application.dto.recordings.segment_timing import RecordingSegmentTiming
from app.application.interfaces.recording_segment_timing_repository import (
    RecordingSegmentTimingRepository,
)

from .models.recording_segment_timing_model import (
    RecordingSegmentTimingModel,
)


class SQLAlchemyRecordingSegmentTimingRepository(RecordingSegmentTimingRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    async def save(self, timing: RecordingSegmentTiming) -> None:
        model = self._session.get(
            RecordingSegmentTimingModel,
            (str(timing.recording_id), timing.segment_index),
        )
        if model is None:
            model = RecordingSegmentTimingModel(
                recording_id=str(timing.recording_id),
                segment_index=timing.segment_index,
                sample_count=timing.sample_count,
                start_sample=timing.start_sample,
            )
            self._session.add(model)
        else:
            model.sample_count, model.start_sample = (
                timing.sample_count,
                timing.start_sample,
            )
        self._session.flush()

    async def get(
        self, recording_id: UUID, segment_index: int
    ) -> RecordingSegmentTiming | None:
        model = self._session.get(
            RecordingSegmentTimingModel, (str(recording_id), segment_index)
        )
        return None if model is None else self._to_dto(model)

    async def list_for_recording(
        self, recording_id: UUID
    ) -> tuple[RecordingSegmentTiming, ...]:
        statement = (
            select(RecordingSegmentTimingModel)
            .where(RecordingSegmentTimingModel.recording_id == str(recording_id))
            .order_by(RecordingSegmentTimingModel.segment_index)
        )
        return tuple(self._to_dto(model) for model in self._session.scalars(statement))

    async def get_last(self, recording_id: UUID) -> RecordingSegmentTiming | None:
        statement = (
            select(RecordingSegmentTimingModel)
            .where(RecordingSegmentTimingModel.recording_id == str(recording_id))
            .order_by(desc(RecordingSegmentTimingModel.segment_index))
            .limit(1)
        )
        model = self._session.scalar(statement)
        return None if model is None else self._to_dto(model)

    @staticmethod
    def _to_dto(model: RecordingSegmentTimingModel) -> RecordingSegmentTiming:
        return RecordingSegmentTiming(
            recording_id=UUID(model.recording_id),
            segment_index=model.segment_index,
            sample_count=model.sample_count,
            start_sample=model.start_sample,
        )
