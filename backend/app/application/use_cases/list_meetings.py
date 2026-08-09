"""List immutable Meeting history read models."""

from app.application.dto.meeting_review import MeetingHistoryItem
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces import UnitOfWorkFactory


class ListMeetingsUseCase:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[MeetingHistoryItem, ...]:
        if not isinstance(limit, int) or not 1 <= limit <= 500 or offset < 0:
            raise ApplicationValidationError("Meeting history query is invalid.")
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.meeting_reviews.list_meetings(
                limit=limit,
                offset=offset,
            )
