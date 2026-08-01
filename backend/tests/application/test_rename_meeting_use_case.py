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


def test_execute_renames_and_saves_the_meeting() -> None:
    """The use case retrieves, renames, and saves its Meeting aggregate."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)
    use_case = RenameMeetingUseCase(unit_of_work_factory)

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
    assert len(unit_of_work_factory.created_unit_of_works) == 1
    assert unit_of_work_factory.created_unit_of_works[0].commit_call_count == 1
    assert unit_of_work_factory.created_unit_of_works[0].exit_call_count == 1


def test_execute_records_a_meeting_renamed_event() -> None:
    """Renaming through the use case records the aggregate domain event."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    asyncio.run(
        RenameMeetingUseCase(unit_of_work_factory).execute(
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
    assert unit_of_work_factory.created_unit_of_works[0].commit_call_count == 1


def test_execute_raises_when_the_meeting_is_missing() -> None:
    """A missing Meeting produces the specified lookup error."""

    repository = FakeMeetingRepository()
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)
    use_case = RenameMeetingUseCase(unit_of_work_factory)

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
    assert unit_of_work_factory.created_unit_of_works[0].commit_call_count == 0
    assert unit_of_work_factory.created_unit_of_works[0].exit_call_count == 1


def test_execute_propagates_domain_validation_errors() -> None:
    """Name validation remains the aggregate's responsibility."""

    meeting = Meeting.create(name="Product review")
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(ValidationError):
        asyncio.run(
            RenameMeetingUseCase(unit_of_work_factory).execute(
                RenameMeetingCommand(meeting_id=meeting.id, new_name="   ")
            )
        )

    assert repository.save_call_count == 0
    assert unit_of_work_factory.created_unit_of_works[0].commit_call_count == 0
    assert unit_of_work_factory.created_unit_of_works[0].exit_call_count == 1


def test_execute_propagates_invalid_state_transition_errors() -> None:
    """Ended Meeting rename attempts remain domain errors."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.end()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(
            RenameMeetingUseCase(unit_of_work_factory).execute(
                RenameMeetingCommand(
                    meeting_id=meeting.id,
                    new_name="Customer interview",
                )
            )
        )

    assert repository.save_call_count == 0
    assert unit_of_work_factory.created_unit_of_works[0].commit_call_count == 0
    assert unit_of_work_factory.created_unit_of_works[0].exit_call_count == 1
