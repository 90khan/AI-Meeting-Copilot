"""Tests for the rename-Meeting application use case."""

import asyncio

import pytest
from app.application.dto import RenameMeetingCommand
from app.application.use_cases import RenameMeetingUseCase
from app.domain.entities import Meeting
from app.domain.events import MeetingRenamed
from app.domain.exceptions import InvalidStateTransitionError, ValidationError
from app.domain.value_objects import MeetingId


class FakeMeetingRepository:
    """In-memory Meeting repository used to test application orchestration."""

    def __init__(self, meetings: list[Meeting] | None = None) -> None:
        """Initialize storage and repository call tracking."""

        self.meetings = meetings or []
        self.get_by_id_call_count = 0
        self.save_call_count = 0

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        """Return a stored Meeting by identity when available."""

        self.get_by_id_call_count += 1
        for meeting in self.meetings:
            if meeting.id == meeting_id:
                return meeting
        return None

    async def save(self, meeting: Meeting) -> None:
        """Record saving a Meeting aggregate."""

        self.save_call_count += 1
        if meeting not in self.meetings:
            self.meetings.append(meeting)

    async def delete(self, meeting: Meeting) -> None:
        """Remove a stored Meeting when it exists."""

        self.meetings.remove(meeting)


def test_execute_renames_and_saves_the_meeting() -> None:
    """The use case retrieves, renames, and saves its Meeting aggregate."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    use_case = RenameMeetingUseCase(repository)

    asyncio.run(
        use_case.execute(
            RenameMeetingCommand(
                meeting_id=meeting.id,
                new_name="Customer interview",
            )
        )
    )

    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 1
    assert meeting.name == "Customer interview"


def test_execute_records_a_meeting_renamed_event() -> None:
    """Renaming through the use case records the aggregate domain event."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])

    asyncio.run(
        RenameMeetingUseCase(repository).execute(
            RenameMeetingCommand(
                meeting_id=meeting.id,
                new_name="Customer interview",
            )
        )
    )

    (event,) = meeting.pull_domain_events()

    assert isinstance(event, MeetingRenamed)
    assert event.aggregate_id == meeting.id
    assert event.old_name == "Product review"
    assert event.new_name == "Customer interview"


def test_execute_raises_when_the_meeting_is_missing() -> None:
    """A missing Meeting produces the specified lookup error."""

    repository = FakeMeetingRepository()
    use_case = RenameMeetingUseCase(repository)

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(
            use_case.execute(
                RenameMeetingCommand(
                    meeting_id=MeetingId.new(),
                    new_name="Customer interview",
                )
            )
        )

    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 0


def test_execute_propagates_domain_validation_errors() -> None:
    """Name validation remains the aggregate's responsibility."""

    meeting = Meeting.create(name="Product review")
    repository = FakeMeetingRepository([meeting])

    with pytest.raises(ValidationError):
        asyncio.run(
            RenameMeetingUseCase(repository).execute(
                RenameMeetingCommand(meeting_id=meeting.id, new_name="   ")
            )
        )

    assert repository.save_call_count == 0


def test_execute_propagates_invalid_state_transition_errors() -> None:
    """Ended Meeting rename attempts remain domain errors."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.end()
    repository = FakeMeetingRepository([meeting])

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(
            RenameMeetingUseCase(repository).execute(
                RenameMeetingCommand(
                    meeting_id=meeting.id,
                    new_name="Customer interview",
                )
            )
        )

    assert repository.save_call_count == 0
