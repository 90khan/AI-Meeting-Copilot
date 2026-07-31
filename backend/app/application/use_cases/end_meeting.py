"""Use case for ending a Meeting aggregate."""

from app.application.dto import EndMeetingCommand
from app.domain.repositories import MeetingRepository


class EndMeetingUseCase:
    """End and persist an existing Meeting aggregate."""

    def __init__(self, repository: MeetingRepository) -> None:
        """Initialize the use case with its Meeting repository dependency."""

        self._repository = repository

    async def execute(self, command: EndMeetingCommand) -> None:
        """End the identified Meeting and persist its changed state."""

        meeting = await self._repository.get_by_id(command.meeting_id)
        if meeting is None:
            raise LookupError("Meeting not found")

        meeting.end()
        await self._repository.save(meeting)
