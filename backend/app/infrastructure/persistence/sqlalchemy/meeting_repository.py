"""SQLAlchemy persistence adapter for Meeting aggregates."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.entities import Meeting
from app.domain.repositories import MeetingRepository
from app.domain.value_objects import MeetingId
from app.infrastructure.persistence.sqlalchemy.mappers import MeetingMapper
from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    TranscriptEntryModel,
)


class SQLAlchemyMeetingRepository(MeetingRepository):
    """Persist Meeting aggregates through an injected SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        """Initialize the repository with a caller-owned session."""

        self._session = session

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        """Return a rehydrated Meeting with its transcripts, if it exists."""

        model = self._load_model(str(meeting_id))
        if model is None:
            return None

        return MeetingMapper.to_domain(model)

    async def save(self, meeting: Meeting) -> None:
        """Insert or synchronize a Meeting aggregate without committing."""

        model = self._load_model(str(meeting.id))
        if model is None:
            self._session.add(MeetingMapper.to_model(meeting))
        else:
            self._update_model(model, meeting)

        self._session.flush()

    async def delete(self, meeting: Meeting) -> None:
        """Delete a Meeting when it exists without committing."""

        model = self._load_model(str(meeting.id))
        if model is None:
            return

        self._session.delete(model)
        self._session.flush()

    def _load_model(self, meeting_id: str) -> MeetingModel | None:
        """Load a Meeting model and its transcript relationship by identity."""

        statement = (
            select(MeetingModel)
            .where(MeetingModel.id == meeting_id)
            .options(selectinload(MeetingModel.transcripts))
        )
        return self._session.scalar(statement)

    @staticmethod
    def _update_model(model: MeetingModel, meeting: Meeting) -> None:
        """Synchronize tracked Meeting and transcript models from an aggregate."""

        model.name = meeting.name
        model.status = meeting.status.value
        model.started_at = meeting.started_at
        model.ended_at = meeting.ended_at

        existing_transcripts = {
            transcript.id: transcript for transcript in model.transcripts
        }
        synchronized_transcripts: list[TranscriptEntryModel] = []
        for sequence, transcript in enumerate(meeting.transcripts):
            transcript_id = str(transcript.id)
            transcript_model = existing_transcripts.pop(transcript_id, None)
            if transcript_model is None:
                transcript_model = TranscriptEntryModel(
                    id=transcript_id,
                    meeting_id=model.id,
                    speaker=transcript.speaker,
                    text=transcript.text,
                    timestamp=transcript.timestamp,
                    sequence=sequence,
                )
            else:
                transcript_model.speaker = transcript.speaker
                transcript_model.text = transcript.text
                transcript_model.timestamp = transcript.timestamp
                transcript_model.sequence = sequence

            synchronized_transcripts.append(transcript_model)

        model.transcripts[:] = synchronized_transcripts
