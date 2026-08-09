"""SQLAlchemy implementation of the application Unit of Work contract."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.application.interfaces.meeting_review_repository import MeetingReviewRepository
from app.application.interfaces.meeting_translation_repository import (
    MeetingTranslationRepository,
)
from app.application.interfaces.recording_repository import RecordingRepository
from app.domain.repositories import MeetingRepository
from app.infrastructure.persistence.sqlalchemy.meeting_repository import (
    SQLAlchemyMeetingRepository,
)
from app.infrastructure.persistence.sqlalchemy.meeting_review_repository import (
    SQLAlchemyMeetingReviewRepository,
)
from app.infrastructure.persistence.sqlalchemy.meeting_translation_repository import (
    SQLAlchemyMeetingTranslationRepository,
)
from app.infrastructure.persistence.sqlalchemy.recording_repository import (
    SQLAlchemyRecordingRepository,
)


class SQLAlchemyUnitOfWork:
    """Coordinate SQLAlchemy repositories through one caller-owned transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Initialize a work unit with a factory for independent sessions."""

        self._session_factory = session_factory
        self._session: Session | None = None
        self._meetings: SQLAlchemyMeetingRepository | None = None
        self._recordings: SQLAlchemyRecordingRepository | None = None
        self._meeting_reviews: SQLAlchemyMeetingReviewRepository | None = None
        self._meeting_translations: SQLAlchemyMeetingTranslationRepository | None = None

    @property
    def meetings(self) -> MeetingRepository:
        """Return the active Meeting repository."""

        if self._meetings is None:
            raise RuntimeError("Unit of Work is not active.")

        return self._meetings

    @property
    def recordings(self) -> RecordingRepository:
        """Return the active recording metadata repository."""

        if self._recordings is None:
            raise RuntimeError("Unit of Work is not active.")
        return self._recordings

    @property
    def meeting_reviews(self) -> MeetingReviewRepository:
        if self._meeting_reviews is None:
            raise RuntimeError("Unit of Work is not active.")
        return self._meeting_reviews

    @property
    def meeting_translations(self) -> MeetingTranslationRepository:
        """Return the active Meeting translation artifact repository."""

        if self._meeting_translations is None:
            raise RuntimeError("Unit of Work is not active.")
        return self._meeting_translations

    @property
    def session(self) -> Session:
        """Return the active SQLAlchemy session."""

        return self._require_session()

    async def __aenter__(self) -> Self:
        """Create one session and its Meeting repository."""

        if self._session is not None:
            raise RuntimeError("Unit of Work is already active.")

        session = self._session_factory()
        self._session = session
        self._meetings = SQLAlchemyMeetingRepository(session)
        self._recordings = SQLAlchemyRecordingRepository(session)
        self._meeting_reviews = SQLAlchemyMeetingReviewRepository(session)
        self._meeting_translations = SQLAlchemyMeetingTranslationRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back failed work and close the session in every case."""

        session = self._require_session()
        try:
            if exc_type is not None:
                session.rollback()
        finally:
            session.close()
            self._meetings = None
            self._recordings = None
            self._meeting_reviews = None
            self._meeting_translations = None
            self._session = None

    async def commit(self) -> None:
        """Commit the active session transaction."""

        self._require_session().commit()

    async def rollback(self) -> None:
        """Roll back the active session transaction."""

        self._require_session().rollback()

    def _require_session(self) -> Session:
        """Return the active session or raise a clear lifecycle error."""

        if self._session is None:
            raise RuntimeError("Unit of Work is not active.")

        return self._session
