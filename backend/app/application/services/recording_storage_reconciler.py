"""One-shot reconciliation of recording metadata with local resources."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from app.application.dto.recordings import (
    RecordingCleanupItemResult,
    RecordingCleanupOutcome,
    RecordingCleanupResult,
    RecordingKeyReference,
    RecordingMetadataRecord,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces import (
    RecordingKeyStore,
    RecordingStorage,
    UnitOfWorkFactory,
)
from app.application.services.recording_deletion_state import (
    mark_deletion_failed,
    mark_storage_missing,
)


class RecordingStorageReconciler:
    """Reconcile known metadata only; orphan completed storage remains deferred."""

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

    async def reconcile(self) -> RecordingCleanupResult:
        _require_utc(self._clock())
        async with self._unit_of_work_factory() as unit_of_work:
            records = await unit_of_work.recordings.list_all()
        items: list[RecordingCleanupItemResult] = []
        for record in records:
            try:
                items.append(await self._reconcile_record(record))
            except asyncio.CancelledError:
                raise
            except Exception:
                items.append(
                    _item(record.metadata.recording_id, RecordingCleanupOutcome.FAILED)
                )
        return RecordingCleanupResult.from_items(items)

    async def _reconcile_record(
        self, record: RecordingMetadataRecord
    ) -> RecordingCleanupItemResult:
        recording_id = record.metadata.recording_id
        try:
            storage_exists = await self._recording_storage.recording_exists(
                recording_id
            )
            key_exists = await self._recording_key_store.exists(
                RecordingKeyReference(value=record.key_reference)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if record.metadata.state is not RecordingState.DELETED:
                await self._mark_failure(recording_id, "reconciliation_failed")
            return _item(recording_id, RecordingCleanupOutcome.FAILED)

        if record.metadata.state is RecordingState.DELETED:
            return await self._cleanup_deleted(record, storage_exists, key_exists)
        if storage_exists and key_exists:
            return _item(recording_id, RecordingCleanupOutcome.SKIPPED)
        if not storage_exists:
            await self._update(recording_id, mark_storage_missing)
            return _item(recording_id, RecordingCleanupOutcome.MISSING)
        await self._mark_failure(recording_id, "key_missing")
        return _item(recording_id, RecordingCleanupOutcome.FAILED)

    async def _cleanup_deleted(
        self,
        record: RecordingMetadataRecord,
        storage_exists: bool,
        key_exists: bool,
    ) -> RecordingCleanupItemResult:
        recording_id = record.metadata.recording_id
        try:
            if storage_exists:
                await self._recording_storage.delete_recording(recording_id)
            if key_exists:
                await self._recording_key_store.delete_key(
                    RecordingKeyReference(value=record.key_reference)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _item(recording_id, RecordingCleanupOutcome.FAILED)
        outcome = (
            RecordingCleanupOutcome.DELETED
            if storage_exists or key_exists
            else RecordingCleanupOutcome.SKIPPED
        )
        return _item(recording_id, outcome)

    async def _update(
        self,
        recording_id: UUID,
        transition: Callable[[RecordingMetadataRecord], RecordingMetadataRecord],
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            current = await unit_of_work.recordings.get_by_id(recording_id)
            if current is None:
                return
            await unit_of_work.recordings.save(transition(current))
            await unit_of_work.commit()

    async def _mark_failure(self, recording_id: UUID, failure_code: str) -> None:
        await self._update(
            recording_id,
            lambda record: mark_deletion_failed(record, failure_code=failure_code),
        )


def _item(
    recording_id: UUID, outcome: RecordingCleanupOutcome
) -> RecordingCleanupItemResult:
    messages = {
        RecordingCleanupOutcome.DELETED: "Recording deleted.",
        RecordingCleanupOutcome.SKIPPED: "Recording skipped.",
        RecordingCleanupOutcome.FAILED: "Recording cleanup failed.",
        RecordingCleanupOutcome.MISSING: "Recording storage was missing.",
    }
    return RecordingCleanupItemResult(
        recording_id=recording_id,
        outcome=outcome,
        message=messages[outcome],
    )


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ApplicationValidationError("Clock must return UTC timestamps.")
