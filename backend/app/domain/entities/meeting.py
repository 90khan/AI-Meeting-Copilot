"""Meeting aggregate root and its core lifecycle."""

from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import uuid4

from app.domain.entities.aggregate_root import AggregateRoot
from app.domain.entities.transcript_entry import TranscriptEntry
from app.domain.events import (
    MeetingCreated,
    MeetingEnded,
    MeetingRenamed,
    MeetingStarted,
    TranscriptAdded,
)
from app.domain.exceptions import (
    InvalidStateTransitionError,
    InvariantViolationError,
    ValidationError,
)
from app.domain.value_objects import MeetingId, MeetingStatus


class Meeting(AggregateRoot[MeetingId]):
    """Aggregate root responsible for a Meeting's lifecycle."""

    def __init__(self, meeting_id: MeetingId, name: str) -> None:
        """Create a draft Meeting with a validated name."""

        super().__init__(meeting_id)
        self._name = self._validate_name(name)
        self._status = MeetingStatus.DRAFT
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._transcripts: list[TranscriptEntry] = []

    @classmethod
    def create(cls, *, meeting_id: MeetingId | None = None, name: str) -> Self:
        """Create a new draft Meeting, generating an identity when needed."""

        meeting = cls(meeting_id or MeetingId.new(), name)
        meeting.record_event(
            MeetingCreated(aggregate_id=meeting.id, meeting_name=meeting.name)
        )
        return meeting

    @classmethod
    def rehydrate(
        cls,
        *,
        meeting_id: MeetingId,
        name: str,
        status: MeetingStatus,
        started_at: datetime | None,
        ended_at: datetime | None,
        transcripts: tuple[TranscriptEntry, ...] = (),
    ) -> Self:
        """Restore persisted Meeting state without recording domain events."""

        cls._validate_rehydrated_lifecycle(status, started_at, ended_at)

        meeting = cls(meeting_id, name)
        meeting._status = status
        meeting._started_at = started_at
        meeting._ended_at = ended_at
        meeting._transcripts = list(transcripts)
        return meeting

    @property
    def name(self) -> str:
        """Return the Meeting's current name."""

        return self._name

    @property
    def status(self) -> MeetingStatus:
        """Return the Meeting's current lifecycle status."""

        return self._status

    @property
    def started_at(self) -> datetime | None:
        """Return when the Meeting started, if it has started."""

        return self._started_at

    @property
    def ended_at(self) -> datetime | None:
        """Return when the Meeting ended, if it has ended."""

        return self._ended_at

    @property
    def transcripts(self) -> tuple[TranscriptEntry, ...]:
        """Return an immutable snapshot of the Meeting transcript."""

        return tuple(self._transcripts)

    def start(self) -> None:
        """Start a draft Meeting."""

        if self._status is not MeetingStatus.DRAFT:
            raise InvalidStateTransitionError("Only a draft Meeting can be started.")

        self._status = MeetingStatus.ACTIVE
        started_at = datetime.now(UTC)
        self._started_at = started_at
        self.record_event(MeetingStarted(aggregate_id=self.id, started_at=started_at))

    def end(self) -> None:
        """End an active Meeting."""

        if self._status is not MeetingStatus.ACTIVE:
            raise InvalidStateTransitionError("Only an active Meeting can be ended.")

        started_at = self._started_at
        ended_at = datetime.now(UTC)
        if started_at is None or ended_at < started_at:
            raise InvariantViolationError("A Meeting cannot end before it has started.")

        self._status = MeetingStatus.ENDED
        self._ended_at = ended_at
        self.record_event(MeetingEnded(aggregate_id=self.id, ended_at=ended_at))

    def rename(self, new_name: str) -> None:
        """Rename a Meeting that has not ended."""

        if self._status is MeetingStatus.ENDED:
            raise InvalidStateTransitionError("An ended Meeting cannot be renamed.")

        old_name = self._name
        self._name = self._validate_name(new_name)
        self.record_event(
            MeetingRenamed(
                aggregate_id=self.id,
                old_name=old_name,
                new_name=self._name,
            )
        )

    def add_transcript(
        self,
        speaker: str,
        text: str,
        timestamp: datetime,
    ) -> TranscriptEntry:
        """Add an immutable transcript entry to a Meeting that has not ended."""

        if self._status is MeetingStatus.ENDED:
            raise InvalidStateTransitionError(
                "An ended Meeting cannot receive transcript entries."
            )

        transcript_entry = TranscriptEntry(uuid4(), speaker, text, timestamp)
        self._transcripts.append(transcript_entry)
        self.record_event(
            TranscriptAdded(
                aggregate_id=self.id,
                transcript_id=transcript_entry.id,
                speaker=transcript_entry.speaker,
            )
        )
        return transcript_entry

    @staticmethod
    def _validate_name(name: str) -> str:
        """Validate and return a non-blank Meeting name."""

        if not name.strip():
            raise ValidationError("Meeting name must not be blank.")

        return name

    @staticmethod
    def _validate_rehydrated_lifecycle(
        status: MeetingStatus,
        started_at: datetime | None,
        ended_at: datetime | None,
    ) -> None:
        """Validate persisted lifecycle state before restoring a Meeting."""

        if not isinstance(status, MeetingStatus):
            raise ValidationError("Meeting status must be a MeetingStatus value.")

        for timestamp in (started_at, ended_at):
            if timestamp is not None:
                Meeting._validate_utc_timestamp(timestamp)

        if status is MeetingStatus.DRAFT:
            if started_at is not None or ended_at is not None:
                raise InvariantViolationError(
                    "A draft Meeting cannot have lifecycle timestamps."
                )
            return

        if status is MeetingStatus.ACTIVE:
            if started_at is None or ended_at is not None:
                raise InvariantViolationError(
                    "An active Meeting requires only a start timestamp."
                )
            return

        if status is MeetingStatus.ENDED:
            if started_at is None or ended_at is None:
                raise InvariantViolationError(
                    "An ended Meeting requires start and end timestamps."
                )
            if ended_at < started_at:
                raise InvariantViolationError(
                    "A Meeting cannot end before it has started."
                )
            return

        raise ValidationError("Meeting status is not supported.")

    @staticmethod
    def _validate_utc_timestamp(timestamp: datetime) -> None:
        """Ensure a persisted timestamp is timezone-aware and uses UTC."""

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValidationError("Meeting timestamps must be timezone-aware.")
        if timestamp.utcoffset() != timedelta(0):
            raise ValidationError("Meeting timestamps must use UTC.")
