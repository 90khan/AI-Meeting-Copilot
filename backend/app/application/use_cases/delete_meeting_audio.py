"""Delete one Meeting's local encrypted recording and its Keychain key."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from app.application.dto.recordings import (
    DeleteMeetingAudioCommand,
    DeleteMeetingAudioResult,
    RecordingKeyReference,
    RecordingMetadataRecord,
    RecordingState,
)
from app.application.exceptions import (
    ApplicationValidationError,
    RecordingKeyNotFoundError,
    RecordingKeyStoreError,
    RecordingSegmentNotFoundError,
    RecordingStorageError,
)
from app.application.interfaces import (
    RecordingKeyStore,
    RecordingStorage,
    UnitOfWorkFactory,
)
from app.application.services import (
    mark_deleted,
    mark_deleting,
    mark_deletion_failed,
)


class DeleteMeetingAudioUseCase:
    """Delete recording resources in independent metadata and external-work phases."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        recording_storage: RecordingStorage,
        recording_key_store: RecordingKeyStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._recording_storage = recording_storage
        self._recording_key_store = recording_key_store
        self._clock = clock

    async def execute(
        self, command: DeleteMeetingAudioCommand
    ) -> DeleteMeetingAudioResult:
        now = self._clock()
        _require_utc(now)
        record = await self._lookup(command)
        if record is None:
            return DeleteMeetingAudioResult(
                meeting_id=command.meeting_id,
                recording_id=None,
                deleted=False,
                already_absent=True,
            )
        if record.metadata.state is RecordingState.DELETED:
            return DeleteMeetingAudioResult(
                meeting_id=command.meeting_id,
                recording_id=record.metadata.recording_id,
                deleted=False,
                already_absent=True,
            )

        await self._mark_deleting(record)
        await self._delete_external(record)
        await self._mark_deleted(record.metadata.recording_id, now)
        return DeleteMeetingAudioResult(
            meeting_id=command.meeting_id,
            recording_id=record.metadata.recording_id,
            deleted=True,
            already_absent=False,
        )

    async def _lookup(
        self, command: DeleteMeetingAudioCommand
    ) -> RecordingMetadataRecord | None:
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.recordings.get_by_meeting_id(command.meeting_id)

    async def _mark_deleting(self, record: RecordingMetadataRecord) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            current = await unit_of_work.recordings.get_by_id(
                record.metadata.recording_id
            )
            if current is None:
                raise LookupError("Recording not found")
            transitioned = mark_deleting(current)
            await unit_of_work.recordings.save(transitioned)
            await unit_of_work.commit()

    async def _delete_external(self, record: RecordingMetadataRecord) -> None:
        recording_id = record.metadata.recording_id
        try:
            await self._recording_storage.delete_recording(recording_id)
        except asyncio.CancelledError:
            raise
        except RecordingSegmentNotFoundError:
            pass
        except Exception as error:
            await self._mark_failure(recording_id, "storage_delete_failed")
            if isinstance(error, RecordingStorageError):
                raise
            raise RecordingStorageError() from error

        try:
            await self._recording_key_store.delete_key(
                RecordingKeyReference(value=record.key_reference)
            )
        except asyncio.CancelledError:
            raise
        except RecordingKeyNotFoundError:
            pass
        except Exception as error:
            await self._mark_failure(recording_id, "key_delete_failed")
            if isinstance(error, RecordingKeyStoreError):
                raise
            raise RecordingKeyStoreError() from error

    async def _mark_deleted(self, recording_id: UUID, now: datetime) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            current = await unit_of_work.recordings.get_by_id(recording_id)
            if current is None:
                return
            transitioned = mark_deleted(current, deleted_at=now)
            await unit_of_work.recordings.save(transitioned)
            await unit_of_work.commit()

    async def _mark_failure(self, recording_id: UUID, failure_code: str) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            current = await unit_of_work.recordings.get_by_id(recording_id)
            if current is None:
                return
            transitioned = mark_deletion_failed(current, failure_code=failure_code)
            await unit_of_work.recordings.save(transitioned)
            await unit_of_work.commit()


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ApplicationValidationError("Clock must return UTC timestamps.")
