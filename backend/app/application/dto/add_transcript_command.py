"""Command for adding one finalized transcript entry to a Meeting."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class AddTranscriptCommand:
    """Carry one transcript entry to an existing Meeting aggregate."""

    meeting_id: MeetingId
    speaker: str
    text: str
    timestamp: datetime
