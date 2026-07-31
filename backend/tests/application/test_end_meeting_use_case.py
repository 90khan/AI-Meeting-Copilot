"""Tests for the end-Meeting application use case."""

import asyncio

import pytest
from app.application.dto import EndMeetingCommand
from app.application.use_cases import EndMeetingUseCase
from app.domain.entities import Meeting
from app.domain.events import MeetingEnded
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId, MeetingStatus


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


def test_execute_ends_and_saves_the_meeting() -> None:
    """The use case retrieves, ends, and saves its Meeting aggregate."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    use_case = EndMeetingUseCase(repository)

    asyncio.run(use_case.execute(EndMeetingCommand(meeting_id=meeting.id)))

    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 1
    assert meeting.status is MeetingStatus.ENDED
    assert meeting.ended_at is not None


def test_execute_records_a_meeting_ended_event() -> None:
    """Ending through the use case records the aggregate domain event."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])

    asyncio.run(
        EndMeetingUseCase(repository).execute(EndMeetingCommand(meeting_id=meeting.id))
    )

    (event,) = meeting.pull_domain_events()

    assert isinstance(event, MeetingEnded)
    assert event.aggregate_id == meeting.id
    assert event.ended_at == meeting.ended_at


def test_execute_raises_when_the_meeting_is_missing() -> None:
    """A missing Meeting produces the specified lookup error."""

    repository = FakeMeetingRepository()
    use_case = EndMeetingUseCase(repository)

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(use_case.execute(EndMeetingCommand(meeting_id=MeetingId.new())))

    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 0


def test_execute_propagates_invalid_state_transition_errors() -> None:
    """Ending a Meeting that is not active remains a domain error."""

    meeting = Meeting.create(name="Product review")
    repository = FakeMeetingRepository([meeting])

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(
            EndMeetingUseCase(repository).execute(
                EndMeetingCommand(meeting_id=meeting.id)
            )
        )

    assert repository.save_call_count == 0
