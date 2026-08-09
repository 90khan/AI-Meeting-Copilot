"""Load one full persisted Meeting transcript read model."""

from app.application.dto.meeting_review import MeetingDetail
from app.application.interfaces import UnitOfWorkFactory
from app.domain.value_objects import MeetingId


class GetMeetingDetailUseCase:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, *, meeting_id: MeetingId) -> MeetingDetail:
        async with self._unit_of_work_factory() as unit_of_work:
            detail = await unit_of_work.meeting_reviews.get_meeting_detail(meeting_id)
        if detail is None:
            raise LookupError("Meeting not found")
        return detail
