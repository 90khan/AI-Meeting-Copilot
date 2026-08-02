"""Update local recording retention scheduling without changing audio resources."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingRetentionPolicy,
    RecordingState,
    UpdateAudioRetentionCommand,
    UpdateAudioRetentionResult,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces import UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError

_SCHEDULING_FAILURE_CODES = frozenset(
    {
        "storage_delete_failed",
        "key_delete_failed",
        "storage_missing",
        "key_missing",
        "reconciliation_failed",
    }
)


class UpdateAudioRetentionUseCase:
    """Recalculate future retention from a UTC clock in one transaction."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    async def execute(
        self, command: UpdateAudioRetentionCommand
    ) -> UpdateAudioRetentionResult:
        now = self._clock()
        _require_utc(now)
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_meeting_id(command.meeting_id)
            if record is None:
                raise LookupError("Recording not found")
            if record.metadata.state is RecordingState.DELETED:
                raise InvalidStateTransitionError(
                    "Deleted recordings cannot update retention."
                )

            expires_at = command.retention_policy.expires_at(now)
            deletion_status = _deletion_status(command)
            failure_code = record.failure_code
            if (
                record.metadata.deletion_status is RecordingDeletionStatus.FAILED
                and failure_code in _SCHEDULING_FAILURE_CODES
            ):
                failure_code = None
            updated = replace(
                record,
                metadata=replace(
                    record.metadata,
                    retention_policy=command.retention_policy,
                    expires_at=expires_at,
                    protected=command.protected,
                    deletion_status=deletion_status,
                ),
                failure_code=failure_code,
            )
            await unit_of_work.recordings.save(updated)
            await unit_of_work.commit()

        return UpdateAudioRetentionResult(
            meeting_id=command.meeting_id,
            recording_id=updated.metadata.recording_id,
            retention_policy=updated.metadata.retention_policy,
            expires_at=updated.metadata.expires_at,
            protected=updated.metadata.protected,
            deletion_status=updated.metadata.deletion_status,
        )


def _deletion_status(command: UpdateAudioRetentionCommand) -> RecordingDeletionStatus:
    if command.protected or command.retention_policy is RecordingRetentionPolicy.MANUAL:
        return RecordingDeletionStatus.NOT_SCHEDULED
    return RecordingDeletionStatus.SCHEDULED


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ApplicationValidationError("Clock must return UTC timestamps.")
