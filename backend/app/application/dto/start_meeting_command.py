"""Input for the start-Meeting use case."""

from dataclasses import dataclass

from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class StartMeetingCommand:
    """Request to start a Meeting by identity."""

    meeting_id: MeetingId
