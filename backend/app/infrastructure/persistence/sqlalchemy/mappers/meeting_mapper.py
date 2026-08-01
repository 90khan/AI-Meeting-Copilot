"""Explicit mappings between Meeting aggregates and SQLAlchemy models."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.entities import Meeting, TranscriptEntry
from app.domain.exceptions import ValidationError
from app.domain.value_objects import MeetingId, MeetingStatus
from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    TranscriptEntryModel,
)


class MeetingMapper:
    """Convert Meeting aggregates to and from detached ORM model graphs."""

    @staticmethod
    def to_model(meeting: Meeting) -> MeetingModel:
        """Create a detached ORM graph representing ``meeting``."""

        meeting_id = str(meeting.id)
        transcripts = [
            TranscriptEntryModel(
                id=str(transcript.id),
                meeting_id=meeting_id,
                speaker=transcript.speaker,
                text=transcript.text,
                timestamp=transcript.timestamp,
                sequence=sequence,
            )
            for sequence, transcript in enumerate(meeting.transcripts)
        ]
        return MeetingModel(
            id=meeting_id,
            name=meeting.name,
            status=meeting.status.value,
            started_at=meeting.started_at,
            ended_at=meeting.ended_at,
            transcripts=transcripts,
        )

    @staticmethod
    def to_domain(model: MeetingModel) -> Meeting:
        """Rehydrate a Meeting aggregate from ``model`` without emitting events."""

        transcripts = tuple(
            MeetingMapper._transcript_to_domain(transcript_model)
            for transcript_model in sorted(
                model.transcripts,
                key=lambda transcript_model: transcript_model.sequence,
            )
        )
        return Meeting.rehydrate(
            meeting_id=MeetingMapper._meeting_id_from_string(model.id),
            name=model.name,
            status=MeetingMapper._meeting_status_from_string(model.status),
            started_at=MeetingMapper._normalize_optional_utc_timestamp(
                model.started_at
            ),
            ended_at=MeetingMapper._normalize_optional_utc_timestamp(model.ended_at),
            transcripts=transcripts,
        )

    @staticmethod
    def _transcript_to_domain(model: TranscriptEntryModel) -> TranscriptEntry:
        """Create a transcript entity from an ORM transcript row."""

        return TranscriptEntry(
            MeetingMapper._uuid_from_string(model.id, "Transcript entry ID"),
            model.speaker,
            model.text,
            MeetingMapper._normalize_utc_timestamp(model.timestamp),
        )

    @staticmethod
    def _meeting_id_from_string(value: str) -> MeetingId:
        """Create a MeetingId from its persisted UUID string."""

        return MeetingId(MeetingMapper._uuid_from_string(value, "Meeting ID"))

    @staticmethod
    def _meeting_status_from_string(value: str) -> MeetingStatus:
        """Create a MeetingStatus from its persisted string value."""

        try:
            return MeetingStatus(value)
        except ValueError as error:
            raise ValidationError("Meeting status is not supported.") from error

    @staticmethod
    def _uuid_from_string(value: str, field_name: str) -> UUID:
        """Parse a persisted UUID string into a UUID value."""

        try:
            return UUID(value)
        except ValueError as error:
            raise ValidationError(f"{field_name} must be a valid UUID.") from error

    @staticmethod
    def _normalize_optional_utc_timestamp(
        timestamp: datetime | None,
    ) -> datetime | None:
        """Normalize an optional persisted timestamp to UTC."""

        if timestamp is None:
            return None

        return MeetingMapper._normalize_utc_timestamp(timestamp)

    @staticmethod
    def _normalize_utc_timestamp(timestamp: datetime) -> datetime:
        """Normalize a persisted timestamp to UTC and reject non-UTC values."""

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return timestamp.replace(tzinfo=UTC)
        if timestamp.utcoffset() != timedelta(0):
            raise ValidationError("Persisted timestamps must use UTC.")

        return timestamp
