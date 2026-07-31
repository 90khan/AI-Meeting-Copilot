"""In-memory persistence adapter for Meeting aggregates."""

from app.domain.entities import Meeting
from app.domain.repositories import MeetingRepository
from app.domain.value_objects import MeetingId


class InMemoryMeetingRepository(MeetingRepository):
    """Store Meeting aggregates in memory for development and tests."""

    def __init__(self) -> None:
        """Initialize empty Meeting storage."""

        self._meetings: dict[MeetingId, Meeting] = {}

    async def get_by_id(self, meeting_id: MeetingId) -> Meeting | None:
        """Return the Meeting identified by ``meeting_id``, if it exists."""

        return self._meetings.get(meeting_id)

    async def save(self, meeting: Meeting) -> None:
        """Insert or replace a Meeting by its identity."""

        self._meetings[meeting.id] = meeting

    async def delete(self, meeting: Meeting) -> None:
        """Remove a Meeting when it is present."""

        self._meetings.pop(meeting.id, None)
