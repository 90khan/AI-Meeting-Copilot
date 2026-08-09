"""Focused SQLAlchemy read queries for Meeting history and transcript review."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.application.dto import AudioSource
from app.application.dto.meeting_review import (
    MeetingDetail,
    MeetingHistoryItem,
    TranscriptReadItem,
)
from app.application.dto.recordings import RecordingDeletionStatus, RecordingState
from app.application.interfaces.meeting_review_repository import MeetingReviewRepository
from app.domain.value_objects import MeetingId, MeetingStatus
from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    RecordingMetadataModel,
    TranscriptEntryModel,
)


class SQLAlchemyMeetingReviewRepository(MeetingReviewRepository):
    """Return read-only DTOs without exposing ORM models."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_meetings(
        self, *, limit: int, offset: int
    ) -> tuple[MeetingHistoryItem, ...]:
        transcript_count = (
            select(
                TranscriptEntryModel.meeting_id,
                func.count(TranscriptEntryModel.id).label("transcript_count"),
            )
            .group_by(TranscriptEntryModel.meeting_id)
            .subquery()
        )
        statement = (
            select(
                MeetingModel,
                RecordingMetadataModel,
                transcript_count.c.transcript_count,
            )
            .outerjoin(
                RecordingMetadataModel,
                RecordingMetadataModel.meeting_id == MeetingModel.id,
            )
            .outerjoin(
                transcript_count,
                transcript_count.c.meeting_id == MeetingModel.id,
            )
            .order_by(MeetingModel.created_at.desc(), MeetingModel.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return tuple(
            self._history_item(meeting, recording, int(count or 0))
            for meeting, recording, count in self._session.execute(statement)
        )

    async def get_meeting_detail(self, meeting_id: MeetingId) -> MeetingDetail | None:
        statement = (
            select(MeetingModel)
            .where(MeetingModel.id == str(meeting_id))
            .options(selectinload(MeetingModel.transcripts))
        )
        meeting = self._session.scalar(statement)
        if meeting is None:
            return None
        recording = self._session.scalar(
            select(RecordingMetadataModel).where(
                RecordingMetadataModel.meeting_id == meeting.id
            )
        )
        transcript = tuple(
            self._transcript_item(item)
            for item in sorted(
                meeting.transcripts,
                key=lambda item: (self._utc(item.timestamp), item.id),
            )
        )
        return MeetingDetail(
            meeting_id=MeetingId(UUID(meeting.id)),
            title=meeting.name,
            status=MeetingStatus(meeting.status),
            created_at=self._utc(meeting.created_at),
            started_at=self._optional_utc(meeting.started_at),
            ended_at=self._optional_utc(meeting.ended_at),
            transcript=transcript,
            recording_available=self._recording_available(recording),
            recording_state=self._recording_state(recording),
            recording_duration_seconds=self._recording_duration(recording),
            audio_expires_at=self._recording_expires_at(recording),
            audio_protected=False if recording is None else recording.protected,
            audio_has_gaps=False if recording is None else recording.has_gaps,
        )

    @classmethod
    def _history_item(
        cls,
        meeting: MeetingModel,
        recording: RecordingMetadataModel | None,
        transcript_count: int,
    ) -> MeetingHistoryItem:
        return MeetingHistoryItem(
            meeting_id=MeetingId(UUID(meeting.id)),
            title=meeting.name,
            status=MeetingStatus(meeting.status),
            created_at=cls._utc(meeting.created_at),
            started_at=cls._optional_utc(meeting.started_at),
            ended_at=cls._optional_utc(meeting.ended_at),
            transcript_count=transcript_count,
            recording_available=cls._recording_available(recording),
            recording_state=cls._recording_state(recording),
            audio_expires_at=cls._recording_expires_at(recording),
            audio_protected=False if recording is None else recording.protected,
        )

    @classmethod
    def _transcript_item(cls, item: TranscriptEntryModel) -> TranscriptReadItem:
        return TranscriptReadItem(
            transcript_id=UUID(item.id),
            text=item.text,
            timestamp=cls._utc(item.timestamp),
            speaker=item.speaker,
            source=AudioSource.MIXED,
        )

    @staticmethod
    def _recording_available(recording: RecordingMetadataModel | None) -> bool:
        return recording is not None and (
            recording.state == RecordingState.COMPLETED.value
            and recording.deletion_status != RecordingDeletionStatus.DELETED.value
        )

    @staticmethod
    def _recording_state(
        recording: RecordingMetadataModel | None,
    ) -> RecordingState | None:
        return None if recording is None else RecordingState(recording.state)

    @staticmethod
    def _recording_duration(recording: RecordingMetadataModel | None) -> float | None:
        return None if recording is None else recording.duration_seconds

    @classmethod
    def _recording_expires_at(
        cls, recording: RecordingMetadataModel | None
    ) -> datetime | None:
        return None if recording is None else cls._optional_utc(recording.expires_at)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @classmethod
    def _optional_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else cls._utc(value)
