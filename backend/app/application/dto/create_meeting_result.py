"""Output from the create-Meeting use case."""

from dataclasses import dataclass

from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateMeetingResult:
    """Identity of the Meeting created by the use case."""

    meeting_id: MeetingId
