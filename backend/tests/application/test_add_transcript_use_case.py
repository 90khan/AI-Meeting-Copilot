"""Tests for the add-transcript application use case."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.application.dto import AddTranscriptCommand
from app.application.use_cases import AddTranscriptUseCase
from app.domain.entities import Meeting
from app.domain.events import TranscriptAdded
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
        """Record saving the supplied Meeting aggregate."""

        self.save_call_count += 1

    async def delete(self, meeting: Meeting) -> None:
        """Remove a stored Meeting aggregate when present."""

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
    """Create a fresh fake Unit of Work for every use-case execution."""

    def __init__(self, repository: FakeMeetingRepository) -> None:
        """Initialize the factory with its repository fake."""

        self._repository = repository
        self.created_unit_of_works: list[FakeUnitOfWork] = []

    def __call__(self) -> FakeUnitOfWork:
        """Create and track one fake Unit of Work."""

        unit_of_work = FakeUnitOfWork(self._repository)
        self.created_unit_of_works.append(unit_of_work)
        return unit_of_work


def _command(
    meeting_id: MeetingId,
    *,
    speaker: str = "Unknown",
    text: str = "Hello",
) -> AddTranscriptCommand:
    return AddTranscriptCommand(
        meeting_id=meeting_id,
        speaker=speaker,
        text=text,
        timestamp=datetime.now(UTC),
    )


def test_execute_adds_and_saves_one_transcript_entry() -> None:
    """The use case adds one aggregate-owned transcript and commits once."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)
    use_case = AddTranscriptUseCase(unit_of_work_factory)

    result = asyncio.run(use_case.execute(_command(meeting.id)))

    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 1
    assert len(unit_of_work_factory.created_unit_of_works) == 1
    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert unit_of_work.commit_call_count == 1
    assert unit_of_work.exit_call_count == 1
    assert len(meeting.transcripts) == 1
    assert result.transcript_id == meeting.transcripts[0].id


def test_execute_leaves_the_transcript_added_event_on_the_aggregate() -> None:
    """The use case neither publishes nor removes aggregate domain events."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    asyncio.run(
        AddTranscriptUseCase(unit_of_work_factory).execute(_command(meeting.id))
    )

    (event,) = meeting.pull_domain_events()
    assert isinstance(event, TranscriptAdded)
    assert event.aggregate_id == meeting.id
    assert event.transcript_id == meeting.transcripts[0].id


def test_execute_raises_when_the_meeting_is_missing() -> None:
    """A missing Meeting produces the specified lookup error without committing."""

    repository = FakeMeetingRepository()
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(
            AddTranscriptUseCase(unit_of_work_factory).execute(
                _command(MeetingId.new())
            )
        )

    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 0
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1


@pytest.mark.parametrize(
    ("speaker", "text"),
    [("", "Hello"), ("Unknown", "")],
)
def test_execute_propagates_transcript_validation_errors(
    speaker: str,
    text: str,
) -> None:
    """Aggregate validation failures propagate without saving or committing."""

    meeting = Meeting.create(name="Product review")
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(ValidationError):
        asyncio.run(
            AddTranscriptUseCase(unit_of_work_factory).execute(
                _command(meeting.id, speaker=speaker, text=text)
            )
        )

    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert repository.save_call_count == 0
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1


def test_execute_propagates_ended_meeting_state_errors() -> None:
    """An ended Meeting cannot receive a transcript or commit a transaction."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    meeting.end()
    meeting.pull_domain_events()
    repository = FakeMeetingRepository([meeting])
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(
            AddTranscriptUseCase(unit_of_work_factory).execute(_command(meeting.id))
        )

    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert repository.save_call_count == 0
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1
