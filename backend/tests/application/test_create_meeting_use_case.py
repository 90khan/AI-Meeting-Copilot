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


class FakeUnitOfWork:
    """Unit of Work fake that tracks transaction and context behavior."""

    def __init__(self, repository: FakeMeetingRepository) -> None:
        """Bind the fake to a Meeting repository."""

        self.meetings = repository
        self.commit_call_count = 0
        self.exit_call_count = 0

    async def __aenter__(self) -> "FakeUnitOfWork":
        """Enter the fake Unit of Work context."""

        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Record context exit without suppressing exceptions."""

        self.exit_call_count += 1

    async def commit(self) -> None:
        """Record a successful transaction commit."""

        self.commit_call_count += 1


class FakeUnitOfWorkFactory:
    """Create a fresh fake Unit of Work for each use-case execution."""

    def __init__(self, repository: FakeMeetingRepository) -> None:
        """Initialize the factory with its repository fake."""

        self._repository = repository
        self.created_unit_of_works: list[FakeUnitOfWork] = []

    def __call__(self) -> FakeUnitOfWork:
        """Create and track one fake Unit of Work."""

        unit_of_work = FakeUnitOfWork(self._repository)
        self.created_unit_of_works.append(unit_of_work)
        return unit_of_work


def test_execute_saves_the_created_meeting_once() -> None:
    """The use case creates a Meeting and persists it through its repository."""

    repository = FakeMeetingRepository()
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)
    use_case = CreateMeetingUseCase(unit_of_work_factory)

    result = asyncio.run(use_case.execute(CreateMeetingCommand(name="Product review")))

    assert repository.save_call_count == 1
    assert len(repository.saved_meetings) == 1
    assert repository.saved_meetings[0].name == "Product review"
    assert result.meeting_id == repository.saved_meetings[0].id
    assert len(unit_of_work_factory.created_unit_of_works) == 1
    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert unit_of_work.commit_call_count == 1
    assert unit_of_work.exit_call_count == 1


def test_execute_propagates_domain_validation_errors_unchanged() -> None:
    """Domain validation remains the aggregate's responsibility."""

    repository = FakeMeetingRepository()
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)
    use_case = CreateMeetingUseCase(unit_of_work_factory)

    with pytest.raises(ValidationError, match="Meeting name must not be blank"):
        asyncio.run(use_case.execute(CreateMeetingCommand(name="   ")))

    assert repository.save_call_count == 0
    assert repository.saved_meetings == []
    assert len(unit_of_work_factory.created_unit_of_works) == 1
    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1


def test_separately_created_use_cases_receive_independent_work_units() -> None:
    """Each use-case execution obtains a fresh Unit of Work from its factory."""

    repository = FakeMeetingRepository()
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)
    first_use_case = CreateMeetingUseCase(unit_of_work_factory)
    second_use_case = CreateMeetingUseCase(unit_of_work_factory)

    asyncio.run(first_use_case.execute(CreateMeetingCommand(name="Product review")))
    asyncio.run(
        second_use_case.execute(CreateMeetingCommand(name="Customer interview"))
    )

    assert len(unit_of_work_factory.created_unit_of_works) == 2
    first_unit_of_work, second_unit_of_work = unit_of_work_factory.created_unit_of_works
    assert first_unit_of_work is not second_unit_of_work
    assert first_unit_of_work.commit_call_count == 1
    assert second_unit_of_work.commit_call_count == 1
