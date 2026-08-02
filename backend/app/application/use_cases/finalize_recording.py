"""Finalize safe recording metadata after a local writer completes."""

from dataclasses import replace

from app.application.dto.recordings import FinalizeRecordingCommand, RecordingState
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
            await unit_of_work.recordings.save(
                replace(
                    record,
                    metadata=replace(
                        record.metadata,
                        state=RecordingState.COMPLETED,
                        duration_seconds=command.duration_seconds,
                        segment_count=command.segment_count,
                        has_gaps=command.has_gaps,
                    ),
                )
            )
            await unit_of_work.commit()
