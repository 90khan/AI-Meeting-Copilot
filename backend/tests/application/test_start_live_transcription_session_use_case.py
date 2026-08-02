"""Tests for live-transcription session-start validation orchestration."""

import asyncio

import pytest
from app.application.dto import StartLiveTranscriptionSessionCommand
from app.application.dto.ai import LanguageCode
from app.application.dto.live_transcription import AudioSource
from app.application.use_cases import StartLiveTranscriptionSessionUseCase
from app.domain.entities import Meeting
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId


class FakeMeetingRepository:
    """In-memory repository fake that tracks validation-only access."""

    def __init__(self, meeting: Meeting | None) -> None:
        """Initialize the repository with an optional Meeting."""

        self._meeting = meeting
        self.get_by_id_call_count = 0
        self.save_call_count = 0

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        """Return the supplied Meeting when its identity matches."""

        self.get_by_id_call_count += 1
        if self._meeting is not None and self._meeting.id == meeting_id:
            return self._meeting
        return None

    async def save(self, meeting: Meeting) -> None:
        """Track unexpected persistence mutations."""

        self.save_call_count += 1

    async def delete(self, meeting: Meeting) -> None:
        """Satisfy the Meeting repository protocol for the fake."""


class FakeUnitOfWork:
    """Unit of Work fake that records context and commit behavior."""

    def __init__(self, repository: FakeMeetingRepository) -> None:
        """Bind the fake to its Meeting repository."""

        self.meetings = repository
        self.commit_call_count = 0
        self.exit_call_count = 0

    async def __aenter__(self) -> "FakeUnitOfWork":
        """Enter the fake Unit of Work context."""

        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Record exit without suppressing an exception."""

        self.exit_call_count += 1

    async def commit(self) -> None:
        """Track unexpected transaction commits."""

        self.commit_call_count += 1


class FakeUnitOfWorkFactory:
    """Create one tracked Unit of Work per use-case invocation."""

    def __init__(self, repository: FakeMeetingRepository) -> None:
        """Initialize the factory with its shared repository fake."""

        self._repository = repository
        self.created_unit_of_works: list[FakeUnitOfWork] = []

    def __call__(self) -> FakeUnitOfWork:
        """Return a fresh tracked Unit of Work."""

        unit_of_work = FakeUnitOfWork(self._repository)
        self.created_unit_of_works.append(unit_of_work)
        return unit_of_work


def _command(meeting_id: MeetingId) -> StartLiveTranscriptionSessionCommand:
    return StartLiveTranscriptionSessionCommand(
        meeting_id=meeting_id,
        language_hint=LanguageCode(value="de-DE"),
        source=AudioSource.SYSTEM_AUDIO,
    )


def test_active_meeting_returns_the_validated_configuration() -> None:
    """An active Meeting is accepted without mutation or a transaction commit."""

    meeting = Meeting.create(name="Product review")
    meeting.start()
    repository = FakeMeetingRepository(meeting)
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    result = asyncio.run(
        StartLiveTranscriptionSessionUseCase(unit_of_work_factory).execute(
            _command(meeting.id)
        )
    )

    assert result.meeting_id == meeting.id
    assert result.language_hint == LanguageCode(value="de-DE")
    assert result.source is AudioSource.SYSTEM_AUDIO
    assert repository.get_by_id_call_count == 1
    assert repository.save_call_count == 0
    assert len(unit_of_work_factory.created_unit_of_works) == 1
    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1


def test_missing_meeting_raises_without_saving_or_committing() -> None:
    """A missing aggregate propagates the standard lookup failure."""

    repository = FakeMeetingRepository(None)
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(
            StartLiveTranscriptionSessionUseCase(unit_of_work_factory).execute(
                _command(MeetingId.new())
            )
        )

    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert repository.save_call_count == 0
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1


@pytest.mark.parametrize("state", ["draft", "ended"])
def test_non_active_meeting_is_rejected_without_mutation(state: str) -> None:
    """Draft and ended Meetings cannot start live transcription."""

    meeting = Meeting.create(name="Product review")
    if state == "ended":
        meeting.start()
        meeting.end()
    repository = FakeMeetingRepository(meeting)
    unit_of_work_factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(InvalidStateTransitionError, match="active Meeting"):
        asyncio.run(
            StartLiveTranscriptionSessionUseCase(unit_of_work_factory).execute(
                _command(meeting.id)
            )
        )

    unit_of_work = unit_of_work_factory.created_unit_of_works[0]
    assert repository.save_call_count == 0
    assert unit_of_work.commit_call_count == 0
    assert unit_of_work.exit_call_count == 1
