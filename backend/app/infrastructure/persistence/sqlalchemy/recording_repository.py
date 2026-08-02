"""SQLAlchemy adapter for local recording metadata."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces.recording_repository import RecordingRepository
from app.domain.value_objects import MeetingId
from app.infrastructure.persistence.sqlalchemy.models import RecordingMetadataModel


class SQLAlchemyRecordingRepository(RecordingRepository):
    """Persist recording metadata in a caller-owned synchronous Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def save(self, metadata: RecordingMetadataRecord) -> None:
        model = self._session.get(
            RecordingMetadataModel, str(metadata.metadata.recording_id)
        )
        if model is None:
            model = RecordingMetadataModel(
                recording_id=str(metadata.metadata.recording_id)
            )
            self._session.add(model)
        self._apply(model, metadata)
        self._session.flush()

    async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None:
        model = self._session.get(RecordingMetadataModel, str(recording_id))
        return None if model is None else self._to_record(model)

    async def get_by_meeting_id(
        self, meeting_id: MeetingId
    ) -> RecordingMetadataRecord | None:
        statement = select(RecordingMetadataModel).where(
            RecordingMetadataModel.meeting_id == str(meeting_id)
        )
        model = self._session.scalar(statement)
        return None if model is None else self._to_record(model)

    async def list_expired(
        self, as_of: datetime
    ) -> tuple[RecordingMetadataRecord, ...]:
        normalized = self._normalize_utc(as_of)
        statement = (
            select(RecordingMetadataModel)
            .where(
                RecordingMetadataModel.expires_at.is_not(None),
                RecordingMetadataModel.expires_at <= normalized,
                RecordingMetadataModel.protected.is_(False),
                RecordingMetadataModel.state != RecordingState.DELETED.value,
                RecordingMetadataModel.deletion_status
                != RecordingDeletionStatus.DELETED.value,
            )
            .order_by(
                RecordingMetadataModel.expires_at,
                RecordingMetadataModel.recording_id,
            )
        )
        return tuple(
            self._to_record(model) for model in self._session.scalars(statement)
        )

    async def delete_metadata(self, recording_id: UUID) -> None:
        model = self._session.get(RecordingMetadataModel, str(recording_id))
        if model is not None:
            self._session.delete(model)
            self._session.flush()

    @staticmethod
    def _apply(model: RecordingMetadataModel, record: RecordingMetadataRecord) -> None:
        metadata = record.metadata
        model.meeting_id = str(metadata.meeting_id)
        model.state = metadata.state.value
        model.retention_policy = metadata.retention_policy.value
        model.created_at = metadata.created_at
        model.expires_at = metadata.expires_at
        model.capture_anchor_utc = metadata.capture_anchor_utc
        model.duration_seconds = metadata.duration_seconds
        model.protected = metadata.protected
        model.consent_confirmed = metadata.consent_confirmed
        model.consent_confirmed_at = metadata.consent_confirmed_at
        model.deletion_status = metadata.deletion_status.value
        model.deleted_at = metadata.deleted_at
        model.encryption_format_version = metadata.encryption_format_version
        model.container_format = metadata.container_format
        model.segment_count = metadata.segment_count
        model.has_gaps = metadata.has_gaps
        model.storage_directory_token = record.storage_directory_token
        model.key_reference = record.key_reference
        model.current_segment_index = record.current_segment_index
        model.failure_code = record.failure_code

    @classmethod
    def _to_record(cls, model: RecordingMetadataModel) -> RecordingMetadataRecord:
        return RecordingMetadataRecord(
            metadata=RecordingMetadata(
                recording_id=UUID(model.recording_id),
                meeting_id=MeetingId(UUID(model.meeting_id)),
                state=RecordingState(model.state),
                retention_policy=RecordingRetentionPolicy(model.retention_policy),
                created_at=cls._normalize_utc(model.created_at),
                expires_at=cls._normalize_optional_utc(model.expires_at),
                capture_anchor_utc=cls._normalize_optional_utc(
                    model.capture_anchor_utc
                ),
                duration_seconds=model.duration_seconds,
                protected=model.protected,
                consent_confirmed=model.consent_confirmed,
                consent_confirmed_at=cls._normalize_optional_utc(
                    model.consent_confirmed_at
                ),
                deletion_status=RecordingDeletionStatus(model.deletion_status),
                deleted_at=cls._normalize_optional_utc(model.deleted_at),
                encryption_format_version=model.encryption_format_version,
                container_format=model.container_format,
                segment_count=model.segment_count,
                has_gaps=model.has_gaps,
            ),
            storage_directory_token=model.storage_directory_token,
            key_reference=model.key_reference,
            current_segment_index=model.current_segment_index,
            failure_code=model.failure_code,
        )

    @staticmethod
    def _normalize_optional_utc(value: datetime | None) -> datetime | None:
        return (
            None
            if value is None
            else SQLAlchemyRecordingRepository._normalize_utc(value)
        )

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        if value.utcoffset() != timedelta(0):
            raise ApplicationValidationError(
                "Persisted recording timestamps must use UTC."
            )
        return value
