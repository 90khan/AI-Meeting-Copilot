"""Input for the create-Meeting use case."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateMeetingCommand:
    """Request to create a Meeting with a name."""

    name: str
