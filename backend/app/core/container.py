"""Application composition root."""

import logging

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.interfaces import UnitOfWork
from app.application.use_cases import (
    CreateMeetingUseCase,
    EndMeetingUseCase,
    RenameMeetingUseCase,
    StartMeetingUseCase,
)
from app.core.config import Settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.database.engine import create_engine_from_settings
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.persistence.sqlalchemy import SQLAlchemyUnitOfWork


class Container:
    """Explicitly wire application dependencies and manage their lifecycle."""

    def __init__(self, settings: Settings) -> None:
        """Create a container using the supplied immutable application settings."""

        self._settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        setup_logging(settings)

    async def start(self) -> None:
        """Create lifecycle-managed persistence resources when needed."""

        if self._engine is not None:
            return

        engine = create_engine_from_settings(self._settings)
        self._engine = engine
        self._session_factory = create_session_factory(engine)

    async def stop(self) -> None:
        """Dispose lifecycle-managed persistence resources when present."""

        engine = self._engine
        if engine is None:
            self._session_factory = None
            return

        try:
            engine.dispose()
        finally:
            self._engine = None
            self._session_factory = None

    def get_settings(self) -> Settings:
        """Return the container's immutable application settings."""

        return self._settings

    def get_logger(self, name: str) -> logging.Logger:
        """Return a module-qualified logger configured by this container."""

        return get_logger(name)

    def get_unit_of_work(self) -> UnitOfWork:
        """Create an independent Unit of Work from active persistence resources."""

        return SQLAlchemyUnitOfWork(self._require_session_factory())

    def get_create_meeting_use_case(self) -> CreateMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting creation use case."""

        self._require_session_factory()
        return CreateMeetingUseCase(self.get_unit_of_work)

    def get_start_meeting_use_case(self) -> StartMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting start use case."""

        self._require_session_factory()
        return StartMeetingUseCase(self.get_unit_of_work)

    def get_rename_meeting_use_case(self) -> RenameMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting rename use case."""

        self._require_session_factory()
        return RenameMeetingUseCase(self.get_unit_of_work)

    def get_end_meeting_use_case(self) -> EndMeetingUseCase:
        """Create a Unit-of-Work-backed Meeting end use case."""

        self._require_session_factory()
        return EndMeetingUseCase(self.get_unit_of_work)

    def _require_session_factory(self) -> sessionmaker[Session]:
        """Return the active session factory or raise a lifecycle error."""

        if self._session_factory is None:
            raise RuntimeError("Container has not been started.")

        return self._session_factory
