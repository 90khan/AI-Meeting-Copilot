"""Use case for renaming a Meeting aggregate."""

from app.application.dto import RenameMeetingCommand
from app.application.interfaces import UnitOfWorkFactory


class RenameMeetingUseCase:
    """Rename and persist an existing Meeting aggregate."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Initialize the use case with its Unit of Work factory."""

        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: RenameMeetingCommand) -> None:
        """Rename the identified Meeting and persist its changed state."""

        async with self._unit_of_work_factory() as unit_of_work:
            meeting = await unit_of_work.meetings.get_by_id(command.meeting_id)
            if meeting is None:
                raise LookupError("Meeting not found")

            meeting.rename(command.new_name)
            await unit_of_work.meetings.save(meeting)
            await unit_of_work.commit()
