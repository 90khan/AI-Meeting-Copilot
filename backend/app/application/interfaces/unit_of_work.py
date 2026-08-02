"""Application contract for transactional work units."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from app.application.interfaces.recording_repository import RecordingRepository
from app.domain.repositories import MeetingRepository


class UnitOfWork(Protocol):
    """Coordinate repository work within one transaction boundary."""

    @property
    def meetings(self) -> MeetingRepository:
        """Return the Meeting repository bound to this work unit."""

        ...

    @property
    def recordings(self) -> RecordingRepository:
        """Return recording metadata storage bound to this work unit."""

        ...

    async def __aenter__(self) -> Self:
        """Activate the work unit."""

        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the work unit without suppressing exceptions."""

        ...

    async def commit(self) -> None:
        """Commit the active transaction."""

        ...

    async def rollback(self) -> None:
        """Roll back the active transaction."""

        ...
