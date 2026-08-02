"""Record a generic local recording failure without native details."""

from dataclasses import replace

from app.application.dto.recordings import MarkRecordingFailedCommand, RecordingState
from app.application.interfaces import UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError


class MarkRecordingFailedUseCase:
    """Transition a non-deleted recording record to failed."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: MarkRecordingFailedCommand) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(command.recording_id)
            if record is None:
                raise LookupError("Recording not found")
            if record.metadata.state is RecordingState.DELETED:
                raise InvalidStateTransitionError("Deleted recordings cannot fail.")
            if (
                record.metadata.state is RecordingState.FAILED
                and record.failure_code == command.failure_code
            ):
                return
            await unit_of_work.recordings.save(
                replace(
                    record,
                    metadata=replace(record.metadata, state=RecordingState.FAILED),
                    failure_code=command.failure_code,
                )
            )
            await unit_of_work.commit()
