"""Domain contract for persisting Meeting aggregates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.domain.entities import Meeting
    from app.domain.value_objects import MeetingId


class MeetingRepository(Protocol):
    """Persistence contract for the Meeting aggregate."""

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        """Return the meeting identified by ``meeting_id``, if it exists."""

        ...

    async def save(self, meeting: Meeting) -> None:
        """Persist the current state of ``meeting``."""

        ...

    async def delete(self, meeting: Meeting) -> None:
        """Remove ``meeting`` from persistence."""

        ...
