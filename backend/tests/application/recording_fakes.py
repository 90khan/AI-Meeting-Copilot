"""Test-only fakes for recording lifecycle application use cases."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.application.dto.recordings.encryption import RecordingKeyReference
from app.domain.entities import Meeting
from app.domain.value_objects import MeetingId


class FakeMeetingRepository:
    def __init__(self, meetings: list[Meeting] | None = None) -> None:
        self.meetings = {meeting.id: meeting for meeting in meetings or []}

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        return self.meetings.get(meeting_id)


class FakeRecordingRepository:
    def __init__(
        self,
        records: list[RecordingMetadataRecord] | None = None,
        save_error: Exception | None = None,
    ) -> None:
        self.records = {
            record.metadata.recording_id: record for record in records or []
        }
        self.save_calls = 0
        self.save_error = save_error

    async def save(self, metadata: RecordingMetadataRecord) -> None:
        self.save_calls += 1
        if self.save_error is not None:
            raise self.save_error
        self.records[metadata.metadata.recording_id] = metadata

    async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None:
        return self.records.get(recording_id)

    async def get_by_meeting_id(
        self, meeting_id: MeetingId
    ) -> RecordingMetadataRecord | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.metadata.meeting_id == meeting_id
            ),
            None,
        )

    async def list_all(self) -> tuple[RecordingMetadataRecord, ...]:
        return tuple(
            sorted(
                self.records.values(),
                key=lambda record: (
                    record.metadata.created_at,
                    record.metadata.recording_id,
                ),
            )
        )


class FakeUnitOfWork:
    def __init__(
        self,
        meetings: list[Meeting] | None = None,
        records: list[RecordingMetadataRecord] | None = None,
        recording_save_error: Exception | None = None,
    ) -> None:
        self.meetings = FakeMeetingRepository(meetings)
        self.recordings = FakeRecordingRepository(records, recording_save_error)
        self.commits = 0
        self.exited = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def commit(self) -> None:
        self.commits += 1


class FakeUnitOfWorkFactory:
    def __init__(self, unit_of_work: FakeUnitOfWork) -> None:
        self.unit_of_work = unit_of_work
        self.calls = 0

    def __call__(self) -> FakeUnitOfWork:
        self.calls += 1
        return self.unit_of_work


class FakeRecordingKeyStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.created: list[UUID] = []
        self.deleted: list[RecordingKeyReference] = []

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference:
        if self.error is not None:
            raise self.error
        self.created.append(recording_id)
        return RecordingKeyReference(value="a" * 24)

    async def delete_key(self, reference: RecordingKeyReference) -> None:
        self.deleted.append(reference)


def recording_record(
    *,
    recording_id: UUID,
    meeting_id: MeetingId,
    state: RecordingState = RecordingState.PENDING,
    consent_confirmed: bool = True,
    failure_code: str | None = None,
) -> RecordingMetadataRecord:
    """Build valid, privacy-safe recording metadata for lifecycle tests."""

    now = datetime(2026, 1, 1, tzinfo=UTC)
    deletion_status = (
        RecordingDeletionStatus.DELETED
        if state is RecordingState.DELETED
        else RecordingDeletionStatus.SCHEDULED
    )
    return RecordingMetadataRecord(
        metadata=RecordingMetadata(
            recording_id=recording_id,
            meeting_id=meeting_id,
            state=state,
            retention_policy=RecordingRetentionPolicy.SEVEN_DAYS,
            created_at=now,
            expires_at=now + timedelta(days=7),
            capture_anchor_utc=None,
            duration_seconds=None,
            protected=False,
            consent_confirmed=consent_confirmed,
            consent_confirmed_at=now if consent_confirmed else None,
            deletion_status=deletion_status,
            deleted_at=now if state is RecordingState.DELETED else None,
            encryption_format_version=1,
            container_format="m4a",
            segment_count=0,
            has_gaps=False,
        ),
        storage_directory_token="opaque-storage-token",
        key_reference="opaque-key-reference",
        current_segment_index=0,
        failure_code=failure_code,
    )
