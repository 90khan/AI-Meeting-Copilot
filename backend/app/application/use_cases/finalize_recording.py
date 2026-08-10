"""Finalize safe recording metadata after a local writer completes."""

from dataclasses import replace
from math import isclose

from app.application.dto.recordings import (
    FinalizeRecordingCommand,
    RecordingMediaFormat,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces import UnitOfWorkFactory
from app.application.services.recording_timing import (
    RECORDING_PLAYBACK_SAMPLE_RATE_HZ,
    total_samples_for_contiguous_timing,
)
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
                total_samples = total_samples_for_contiguous_timing(
                    recording_id=command.recording_id,
                    timings=timings,
                )
                segment_count = len(timings)
                duration_seconds = total_samples / RECORDING_PLAYBACK_SAMPLE_RATE_HZ
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
