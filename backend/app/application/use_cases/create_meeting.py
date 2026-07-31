"""Use case for creating a Meeting aggregate."""

from app.application.dto import CreateMeetingCommand, CreateMeetingResult
from app.domain.entities import Meeting
from app.domain.repositories import MeetingRepository


class CreateMeetingUseCase:
    """Create and persist a Meeting aggregate."""

    def __init__(self, repository: MeetingRepository) -> None:
        """Initialize the use case with its Meeting repository dependency."""

        self._repository = repository

    async def execute(self, command: CreateMeetingCommand) -> CreateMeetingResult:
        """Create a Meeting, persist it, and return its identity."""

        meeting = Meeting.create(name=command.name)
        await self._repository.save(meeting)
        return CreateMeetingResult(meeting_id=meeting.id)
