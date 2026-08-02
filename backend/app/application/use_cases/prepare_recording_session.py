"""Prepare opt-in recording metadata without starting audio capture."""

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import UUID

from app.application.dto.recordings import (
    PrepareRecordingSessionCommand,
    PrepareRecordingSessionResult,
    RecordingDeletionStatus,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces import RecordingKeyStore, UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingStatus


class PrepareRecordingSessionUseCase:
    """Validate consent and persist one pending recording metadata record."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        recording_key_store: RecordingKeyStore,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._recording_key_store = recording_key_store
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def execute(
        self, command: PrepareRecordingSessionCommand
    ) -> PrepareRecordingSessionResult:
        if not command.recording_enabled:
            return PrepareRecordingSessionResult(
                enabled=False,
                recording_id=None,
                meeting_id=command.meeting_id,
                retention_policy=None,
                expires_at=None,
            )
        created_at = self._clock()
        if created_at.tzinfo is None or created_at.utcoffset() != timedelta(0):
            raise ApplicationValidationError("Clock must return UTC timestamps.")
        async with self._unit_of_work_factory() as unit_of_work:
            meeting = await unit_of_work.meetings.get_by_id(command.meeting_id)
            if meeting is None:
                raise LookupError("Meeting not found")
            if meeting.status is not MeetingStatus.ACTIVE:
                raise InvalidStateTransitionError(
                    "Recording preparation requires an active Meeting."
                )
            existing = await unit_of_work.recordings.get_by_meeting_id(
                command.meeting_id
            )
            if (
                existing is not None
                and existing.metadata.state is not RecordingState.DELETED
            ):
                raise InvalidStateTransitionError(
                    "A recording is already active for this Meeting."
                )
            recording_id = self._uuid_factory()
            key_reference = await self._recording_key_store.create_key(recording_id)
            expires_at = command.retention_policy.expires_at(created_at)
            deletion_status = (
                RecordingDeletionStatus.NOT_SCHEDULED
                if command.retention_policy is RecordingRetentionPolicy.MANUAL
                else RecordingDeletionStatus.SCHEDULED
            )
            metadata = RecordingMetadata(
                recording_id=recording_id,
                meeting_id=command.meeting_id,
                state=RecordingState.PENDING,
                retention_policy=command.retention_policy,
                created_at=created_at,
                expires_at=expires_at,
                capture_anchor_utc=None,
                duration_seconds=None,
                protected=command.protected,
                consent_confirmed=command.consent_confirmed,
                consent_confirmed_at=command.consent_confirmed_at,
                deletion_status=deletion_status,
                deleted_at=None,
                encryption_format_version=1,
                container_format="m4a",
                segment_count=0,
                has_gaps=False,
            )
            try:
                await unit_of_work.recordings.save(
                    RecordingMetadataRecord(
                        metadata=metadata,
                        storage_directory_token=str(self._uuid_factory()),
                        key_reference=key_reference.value,
                        current_segment_index=0,
                    )
                )
                await unit_of_work.commit()
            except Exception:
                with suppress(Exception):
                    await self._recording_key_store.delete_key(key_reference)
                raise
        return PrepareRecordingSessionResult(
            enabled=True,
            recording_id=recording_id,
            meeting_id=command.meeting_id,
            retention_policy=command.retention_policy,
            expires_at=expires_at,
        )
