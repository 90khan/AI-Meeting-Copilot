"""Read-only query contract for persisted Meeting review data."""

from typing import Protocol

from app.application.dto.meeting_review import MeetingDetail, MeetingHistoryItem
from app.domain.value_objects import MeetingId


class MeetingReviewRepository(Protocol):
    async def list_meetings(
        self, *, limit: int, offset: int
    ) -> tuple[MeetingHistoryItem, ...]: ...

    async def get_meeting_detail(
        self, meeting_id: MeetingId
    ) -> MeetingDetail | None: ...
