"""Input for the rename-Meeting use case."""

from dataclasses import dataclass

from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class RenameMeetingCommand:
    """Request to rename a Meeting by identity."""

    meeting_id: MeetingId
    new_name: str
