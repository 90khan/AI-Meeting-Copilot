"""Input for the end-Meeting use case."""

from dataclasses import dataclass

from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class EndMeetingCommand:
    """Request to end a Meeting by identity."""

    meeting_id: MeetingId
