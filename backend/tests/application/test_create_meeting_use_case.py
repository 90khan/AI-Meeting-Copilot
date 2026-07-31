"""Tests for the create-Meeting application use case."""

import asyncio

import pytest
from app.application.dto import CreateMeetingCommand
from app.application.use_cases import CreateMeetingUseCase
from app.domain.entities import Meeting
from app.domain.exceptions import ValidationError
from app.domain.value_objects import MeetingId


class FakeMeetingRepository:
    """In-memory Meeting repository used to test application orchestration."""

    def __init__(self) -> None:
        """Initialize empty storage and save-call tracking."""

        self.saved_meetings: list[Meeting] = []
        self.save_call_count = 0

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        """Return a stored Meeting by identity when available."""

        for meeting in self.saved_meetings:
            if meeting.id == meeting_id:
                return meeting
        return None

    async def save(self, meeting: Meeting) -> None:
        """Store a Meeting and record the save call."""

        self.save_call_count += 1
        self.saved_meetings.append(meeting)

    async def delete(self, meeting: Meeting) -> None:
        """Remove a stored Meeting when it exists."""

        self.saved_meetings.remove(meeting)


def test_execute_saves_the_created_meeting_once() -> None:
    """The use case creates a Meeting and persists it through its repository."""

    repository = FakeMeetingRepository()
    use_case = CreateMeetingUseCase(repository)

    result = asyncio.run(use_case.execute(CreateMeetingCommand(name="Product review")))

    assert repository.save_call_count == 1
    assert len(repository.saved_meetings) == 1
    assert repository.saved_meetings[0].name == "Product review"
    assert result.meeting_id == repository.saved_meetings[0].id


def test_execute_propagates_domain_validation_errors_unchanged() -> None:
    """Domain validation remains the aggregate's responsibility."""

    repository = FakeMeetingRepository()
    use_case = CreateMeetingUseCase(repository)

    with pytest.raises(ValidationError, match="Meeting name must not be blank"):
        asyncio.run(use_case.execute(CreateMeetingCommand(name="   ")))

    assert repository.save_call_count == 0
    assert repository.saved_meetings == []
