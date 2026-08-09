"""Tests for the SQLAlchemy Unit of Work adapter."""

import asyncio
from collections.abc import Iterator

import pytest
from app.domain.entities import Meeting
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from app.infrastructure.persistence.sqlalchemy.meeting_translation_repository import (
    SQLAlchemyMeetingTranslationRepository,
)
from app.infrastructure.persistence.sqlalchemy.recording_repository import (
    SQLAlchemyRecordingRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    """Provide a session factory backed by isolated in-memory SQLite metadata."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_context_creates_a_meeting_repository(
    session_factory: sessionmaker[Session],
) -> None:
    """Entering a work unit creates one active repository and session."""

    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async def exercise_context() -> None:
        async with unit_of_work as active_unit_of_work:
            assert active_unit_of_work.meetings is not None
            assert active_unit_of_work.session is not None

    asyncio.run(exercise_context())


def test_context_creates_a_session_bound_recording_repository(
    session_factory: sessionmaker[Session],
) -> None:
    """Recording metadata access follows the same active-context lifecycle."""

    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async def exercise_context() -> None:
        async with unit_of_work as active_unit_of_work:
            assert isinstance(
                active_unit_of_work.recordings, SQLAlchemyRecordingRepository
            )

    asyncio.run(exercise_context())

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.recordings


def test_context_creates_a_session_bound_translation_artifact_repository(
    session_factory: sessionmaker[Session],
) -> None:
    """Translation artifact access follows the active-context lifecycle."""

    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async def exercise_context() -> None:
        async with unit_of_work as active_unit_of_work:
            assert isinstance(
                active_unit_of_work.meeting_translations,
                SQLAlchemyMeetingTranslationRepository,
            )

    asyncio.run(exercise_context())

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.meeting_translations


def test_commit_persists_data(session_factory: sessionmaker[Session]) -> None:
    """Explicit commit persists repository changes across work units."""

    meeting = Meeting.create(name="Product review")
    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async def persist_meeting() -> None:
        async with unit_of_work:
            await unit_of_work.meetings.save(meeting)
            await unit_of_work.commit()

    async def load_meeting() -> Meeting | None:
        async with SQLAlchemyUnitOfWork(session_factory) as verification_unit:
            return await verification_unit.meetings.get_by_id(meeting.id)

    asyncio.run(persist_meeting())

    loaded_meeting = asyncio.run(load_meeting())

    assert loaded_meeting is not None
    assert loaded_meeting.id == meeting.id


def test_exit_without_commit_leaves_data_uncommitted(
    session_factory: sessionmaker[Session],
) -> None:
    """Closing an uncommitted work unit leaves its changes unavailable."""

    meeting = Meeting.create(name="Product review")

    async def save_without_commit() -> None:
        async with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
            await unit_of_work.meetings.save(meeting)

    async def load_meeting() -> Meeting | None:
        async with SQLAlchemyUnitOfWork(session_factory) as verification_unit:
            return await verification_unit.meetings.get_by_id(meeting.id)

    asyncio.run(save_without_commit())

    assert asyncio.run(load_meeting()) is None


def test_exception_triggers_rollback(session_factory: sessionmaker[Session]) -> None:
    """An exception exits the work unit after rolling back its changes."""

    meeting = Meeting.create(name="Product review")

    async def fail_after_save() -> None:
        async with SQLAlchemyUnitOfWork(session_factory) as unit_of_work:
            await unit_of_work.meetings.save(meeting)
            raise ValueError("expected failure")

    async def load_meeting() -> Meeting | None:
        async with SQLAlchemyUnitOfWork(session_factory) as verification_unit:
            return await verification_unit.meetings.get_by_id(meeting.id)

    with pytest.raises(ValueError, match="expected failure"):
        asyncio.run(fail_after_save())

    assert asyncio.run(load_meeting()) is None


def test_session_and_repository_access_after_exit_raise_runtime_error(
    session_factory: sessionmaker[Session],
) -> None:
    """Work-unit resources are invalid outside the active context."""

    unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    asyncio.run(_enter_and_exit(unit_of_work))

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.meetings
    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.session
    with pytest.raises(RuntimeError, match="not active"):
        asyncio.run(unit_of_work.commit())
    with pytest.raises(RuntimeError, match="not active"):
        asyncio.run(unit_of_work.rollback())


def test_session_closes_after_context_exit(
    session_factory: sessionmaker[Session],
) -> None:
    """A session obtained in context is closed when the work unit exits."""

    class TrackingSession(Session):
        """Session that records whether close has been called."""

        was_closed = False

        def close(self) -> None:
            """Record the close operation before delegating to SQLAlchemy."""

            self.was_closed = True
            super().close()

    tracking_factory = sessionmaker(
        bind=session_factory.kw["bind"],
        class_=TrackingSession,
    )
    unit_of_work = SQLAlchemyUnitOfWork(tracking_factory)
    session: TrackingSession | None = None

    async def capture_session() -> None:
        nonlocal session
        async with unit_of_work:
            active_session = unit_of_work.session
            assert isinstance(active_session, TrackingSession)
            session = active_session

    asyncio.run(capture_session())

    assert session is not None
    assert session.was_closed is True


def test_separate_work_units_use_separate_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    """Independent work units never share a Session instance."""

    first_unit_of_work = SQLAlchemyUnitOfWork(session_factory)
    second_unit_of_work = SQLAlchemyUnitOfWork(session_factory)

    async def compare_sessions() -> None:
        async with first_unit_of_work:
            first_session = first_unit_of_work.session
            async with second_unit_of_work:
                assert first_session is not second_unit_of_work.session

    asyncio.run(compare_sessions())


async def _enter_and_exit(unit_of_work: SQLAlchemyUnitOfWork) -> None:
    """Enter and exit a Unit of Work for lifecycle assertions."""

    async with unit_of_work:
        pass
