"""Tests for the SQLAlchemy Meeting repository adapter."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from app.domain.entities import Meeting
from app.domain.events import MeetingCreated
from app.domain.value_objects import MeetingStatus
from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy import SQLAlchemyMeetingRepository
from app.infrastructure.persistence.sqlalchemy.models import MeetingModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session() -> Iterator[Session]:
    """Provide an isolated SQLite session with test-only schema creation."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        try:
            yield session
        finally:
            session.rollback()

    engine.dispose()


def test_save_new_meeting_then_loads_a_domain_aggregate(session: Session) -> None:
    """Saving a new Meeting flushes ORM state and returns no ORM model to callers."""

    repository = SQLAlchemyMeetingRepository(session)
    meeting = Meeting.create(name="Product review")

    asyncio.run(repository.save(meeting))
    loaded_meeting = asyncio.run(repository.get_by_id(meeting.id))

    assert loaded_meeting is not None
    assert isinstance(loaded_meeting, Meeting)
    assert not isinstance(loaded_meeting, MeetingModel)
    assert loaded_meeting.id == meeting.id
    assert loaded_meeting.name == "Product review"


def test_save_updates_an_existing_meeting(session: Session) -> None:
    """Saving a tracked Meeting updates its persisted lifecycle and name fields."""

    repository = SQLAlchemyMeetingRepository(session)
    meeting = Meeting.create(name="Product review")
    asyncio.run(repository.save(meeting))
    meeting.start()
    meeting.rename("Customer interview")

    asyncio.run(repository.save(meeting))
    session.expire_all()
    loaded_meeting = asyncio.run(repository.get_by_id(meeting.id))

    assert loaded_meeting is not None
    assert loaded_meeting.name == "Customer interview"
    assert loaded_meeting.status is MeetingStatus.ACTIVE
    assert loaded_meeting.started_at == meeting.started_at


def test_save_persists_transcripts_in_aggregate_order(session: Session) -> None:
    """Transcript child rows are persisted and rehydrated by their sequence."""

    repository = SQLAlchemyMeetingRepository(session)
    meeting = Meeting.create(name="Product review")
    first_entry = meeting.add_transcript(
        speaker="Alex",
        text="Welcome everyone.",
        timestamp=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )
    second_entry = meeting.add_transcript(
        speaker="Jordan",
        text="Thank you.",
        timestamp=datetime(2026, 8, 1, 9, 1, tzinfo=UTC),
    )

    asyncio.run(repository.save(meeting))
    session.expire_all()
    loaded_meeting = asyncio.run(repository.get_by_id(meeting.id))

    assert loaded_meeting is not None
    assert loaded_meeting.transcripts == (first_entry, second_entry)


def test_get_by_id_returns_none_when_the_meeting_is_missing(session: Session) -> None:
    """Lookup returns None when no Meeting row exists."""

    repository = SQLAlchemyMeetingRepository(session)

    assert (
        asyncio.run(repository.get_by_id(Meeting.create(name="Product review").id))
        is None
    )


def test_delete_removes_an_existing_meeting(session: Session) -> None:
    """Deleting an existing Meeting flushes its removal without committing."""

    repository = SQLAlchemyMeetingRepository(session)
    meeting = Meeting.create(name="Product review")
    asyncio.run(repository.save(meeting))

    asyncio.run(repository.delete(meeting))

    assert asyncio.run(repository.get_by_id(meeting.id)) is None


def test_delete_missing_meeting_is_a_no_op(session: Session) -> None:
    """Deleting a Meeting without a row does not raise or flush a model removal."""

    repository = SQLAlchemyMeetingRepository(session)

    asyncio.run(repository.delete(Meeting.create(name="Product review")))


def test_save_flushes_without_committing(session: Session) -> None:
    """Flushed repository changes remain reversible by the caller's rollback."""

    repository = SQLAlchemyMeetingRepository(session)
    meeting = Meeting.create(name="Product review")

    asyncio.run(repository.save(meeting))

    assert session.get(MeetingModel, str(meeting.id)) is not None

    session.rollback()

    assert session.get(MeetingModel, str(meeting.id)) is None


def test_save_preserves_pending_events_and_rehydration_emits_none(
    session: Session,
) -> None:
    """The adapter neither pulls source events nor records rehydration events."""

    repository = SQLAlchemyMeetingRepository(session)
    meeting = Meeting.create(name="Product review")

    asyncio.run(repository.save(meeting))
    loaded_meeting = asyncio.run(repository.get_by_id(meeting.id))

    (event,) = meeting.pull_domain_events()

    assert isinstance(event, MeetingCreated)
    assert loaded_meeting is not None
    assert loaded_meeting.pull_domain_events() == ()
