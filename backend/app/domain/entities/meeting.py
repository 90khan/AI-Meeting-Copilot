"""Meeting aggregate root and its core lifecycle."""

from datetime import UTC, datetime
from typing import Self

from app.domain.entities.aggregate_root import AggregateRoot
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

    @classmethod
    def create(cls, *, meeting_id: MeetingId | None = None, name: str) -> Self:
        """Create a new draft Meeting, generating an identity when needed."""

        return cls(meeting_id or MeetingId.new(), name)

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

    def start(self) -> None:
        """Start a draft Meeting."""

        if self._status is not MeetingStatus.DRAFT:
            raise InvalidStateTransitionError("Only a draft Meeting can be started.")

        self._status = MeetingStatus.ACTIVE
        self._started_at = datetime.now(UTC)
        # A MeetingStarted event will be recorded here once concrete events exist.

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
        # A MeetingEnded event will be recorded here once concrete events exist.

    def rename(self, new_name: str) -> None:
        """Rename a Meeting that has not ended."""

        if self._status is MeetingStatus.ENDED:
            raise InvalidStateTransitionError("An ended Meeting cannot be renamed.")

        self._name = self._validate_name(new_name)
        # A MeetingRenamed event will be recorded here once concrete events exist.

    @staticmethod
    def _validate_name(name: str) -> str:
        """Validate and return a non-blank Meeting name."""

        if not name.strip():
            raise ValidationError("Meeting name must not be blank.")

        return name
