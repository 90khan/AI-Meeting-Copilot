"""One-shot retention cleanup for expired local recordings."""

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
)
from app.application.exceptions import (
    ApplicationValidationError,
    RecordingKeyNotFoundError,
    RecordingSegmentNotFoundError,
)
from app.application.interfaces import (
    RecordingKeyStore,
    RecordingStorage,
    UnitOfWorkFactory,
)
from app.application.services.recording_deletion_state import (
    mark_deleted,
    mark_deleting,
    mark_deletion_failed,
)


class RecordingRetentionCleanupService:
    """Delete expired recording data without spanning external work in a UoW."""

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

    async def run_once(self) -> RecordingCleanupResult:
        """Delete each repository-selected expired recording in repository order."""

        now = self._clock()
        _require_utc(now)
        async with self._unit_of_work_factory() as unit_of_work:
            expired_records = await unit_of_work.recordings.list_expired(as_of=now)

        items: list[RecordingCleanupItemResult] = []
        for expired_record in expired_records:
            item = await self._cleanup_record(
                recording_id=expired_record.metadata.recording_id,
                deleted_at=now,
            )
            items.append(item)
        return RecordingCleanupResult.from_items(items)

    async def _cleanup_record(
        self,
        *,
        recording_id: UUID,
        deleted_at: datetime,
    ) -> RecordingCleanupItemResult:
        try:
            record = await self._mark_deleting(recording_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._mark_failure(
                recording_id=recording_id,
                failure_code="reconciliation_failed",
            )
            return _failed_item(recording_id)
        if record is None:
            return _missing_item(recording_id)

        storage_missing = False
        try:
            await self._recording_storage.delete_recording(recording_id)
        except asyncio.CancelledError:
            raise
        except RecordingSegmentNotFoundError:
            storage_missing = True
        except Exception:
            await self._mark_failure(
                recording_id=recording_id,
                failure_code="storage_delete_failed",
            )
            return _failed_item(recording_id)

        try:
            key_reference = RecordingKeyReference(value=record.key_reference)
            await self._recording_key_store.delete_key(key_reference)
        except asyncio.CancelledError:
            raise
        except RecordingKeyNotFoundError:
            pass
        except Exception:
            await self._mark_failure(
                recording_id=recording_id,
                failure_code="key_delete_failed",
            )
            return _failed_item(recording_id)

        try:
            finalized = await self._mark_deleted(
                recording_id=recording_id,
                deleted_at=deleted_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._mark_failure(
                recording_id=recording_id,
                failure_code="reconciliation_failed",
            )
            return _failed_item(recording_id)
        if not finalized:
            return _missing_item(recording_id)
        if storage_missing:
            return RecordingCleanupItemResult(
                recording_id=recording_id,
                outcome=RecordingCleanupOutcome.MISSING,
                message="Recording storage was missing.",
            )
        return RecordingCleanupItemResult(
            recording_id=recording_id,
            outcome=RecordingCleanupOutcome.DELETED,
            message="Recording deleted.",
        )

    async def _mark_deleting(
        self, recording_id: UUID
    ) -> RecordingMetadataRecord | None:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(recording_id)
            if record is None:
                return None
            transitioned = mark_deleting(record)
            await unit_of_work.recordings.save(transitioned)
            await unit_of_work.commit()
            return transitioned

    async def _mark_deleted(self, *, recording_id: UUID, deleted_at: datetime) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(recording_id)
            if record is None:
                return False
            transitioned = mark_deleted(record, deleted_at=deleted_at)
            await unit_of_work.recordings.save(transitioned)
            await unit_of_work.commit()
            return True

    async def _mark_failure(self, *, recording_id: UUID, failure_code: str) -> None:
        """Persist failure state best-effort."""

        try:
            async with self._unit_of_work_factory() as unit_of_work:
                record = await unit_of_work.recordings.get_by_id(recording_id)
                if record is None:
                    return
                transitioned = mark_deletion_failed(record, failure_code=failure_code)
                await unit_of_work.recordings.save(transitioned)
                await unit_of_work.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            return


def _missing_item(recording_id: UUID) -> RecordingCleanupItemResult:
    return RecordingCleanupItemResult(
        recording_id=recording_id,
        outcome=RecordingCleanupOutcome.MISSING,
        message="Recording storage was missing.",
    )


def _failed_item(recording_id: UUID) -> RecordingCleanupItemResult:
    return RecordingCleanupItemResult(
        recording_id=recording_id,
        outcome=RecordingCleanupOutcome.FAILED,
        message="Recording cleanup failed.",
    )


def _require_utc(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ApplicationValidationError("Recording cleanup clock must use UTC.")
