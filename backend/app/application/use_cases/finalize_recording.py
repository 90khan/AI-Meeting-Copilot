"""Finalize safe recording metadata after a local writer completes."""

from dataclasses import replace
from math import isclose

from app.application.dto.recordings import (
    FinalizeRecordingCommand,
    RecordingMediaFormat,
    RecordingSegmentTiming,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces import UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError


class FinalizeRecordingUseCase:
    """Persist completed recording duration and segment metadata."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: FinalizeRecordingCommand) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(command.recording_id)
            if record is None:
                raise LookupError("Recording not found")
            if record.metadata.state not in {
                RecordingState.RECORDING,
                RecordingState.FINALIZING,
            }:
                raise InvalidStateTransitionError("Recording cannot be finalized.")
            duration_seconds = command.duration_seconds
            segment_count = command.segment_count
            if (
                record.metadata.container_format
                is RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1
            ):
                timing_repository = unit_of_work.recording_segment_timings
                timings = await timing_repository.list_for_recording(
                    command.recording_id
                )
                segment_count, duration_seconds = self._derive_v1_completion(timings)
                if command.segment_count != segment_count or not isclose(
                    command.duration_seconds,
                    duration_seconds,
                    rel_tol=0.0,
                    abs_tol=1e-6,
                ):
                    raise ApplicationValidationError(
                        "Recording finalization metadata is inconsistent."
                    )
            await unit_of_work.recordings.save(
                replace(
                    record,
                    metadata=replace(
                        record.metadata,
                        state=RecordingState.COMPLETED,
                        duration_seconds=duration_seconds,
                        segment_count=segment_count,
                        has_gaps=command.has_gaps,
                    ),
                )
            )
            await unit_of_work.commit()

    @staticmethod
    def _derive_v1_completion(
        timings: tuple[RecordingSegmentTiming, ...],
    ) -> tuple[int, float]:
        if not timings:
            raise ApplicationValidationError("Recording timing metadata is invalid.")

        previous: RecordingSegmentTiming | None = None
        total_samples = 0
        for timing in timings:
            if timing.sample_count <= 0 or (
                previous is None
                and (timing.segment_index != 0 or timing.start_sample != 0)
            ):
                raise ApplicationValidationError(
                    "Recording timing metadata is invalid."
                )
            if previous is not None and (
                timing.segment_index != previous.segment_index + 1
                or timing.start_sample != previous.start_sample + previous.sample_count
            ):
                raise ApplicationValidationError(
                    "Recording timing metadata is invalid."
                )
            total_samples += timing.sample_count
            previous = timing

        return len(timings), total_samples / 16_000.0
