"""Transition prepared metadata to the recording state."""

from dataclasses import replace

from app.application.dto.recordings import (
    MarkRecordingStartedCommand,
    RecordingState,
)
from app.application.interfaces import UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError


class MarkRecordingStartedUseCase:
    """Persist the UTC capture anchor after native capture has started."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: MarkRecordingStartedCommand) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(command.recording_id)
            if record is None:
                raise LookupError("Recording not found")
            if record.metadata.state is not RecordingState.PENDING:
                raise InvalidStateTransitionError("Recording must be pending to start.")
            if not record.metadata.consent_confirmed:
                raise InvalidStateTransitionError(
                    "Recording requires confirmed consent."
                )
            await unit_of_work.recordings.save(
                replace(
                    record,
                    metadata=replace(
                        record.metadata,
                        state=RecordingState.RECORDING,
                        capture_anchor_utc=command.capture_anchor_utc,
                    ),
                )
            )
            await unit_of_work.commit()
