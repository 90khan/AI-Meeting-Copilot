"""Use case for renaming a Meeting aggregate."""

from app.application.dto import RenameMeetingCommand
from app.domain.repositories import MeetingRepository


class RenameMeetingUseCase:
    """Rename and persist an existing Meeting aggregate."""

    def __init__(self, repository: MeetingRepository) -> None:
        """Initialize the use case with its Meeting repository dependency."""

        self._repository = repository

    async def execute(self, command: RenameMeetingCommand) -> None:
        """Rename the identified Meeting and persist its changed state."""

        meeting = await self._repository.get_by_id(command.meeting_id)
        if meeting is None:
            raise LookupError("Meeting not found")

        meeting.rename(command.new_name)
        await self._repository.save(meeting)
