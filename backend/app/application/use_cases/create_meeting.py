"""Use case for creating a Meeting aggregate."""

from app.application.dto import CreateMeetingCommand, CreateMeetingResult
from app.application.interfaces import UnitOfWorkFactory
from app.domain.entities import Meeting


class CreateMeetingUseCase:
    """Create and persist a Meeting aggregate."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Initialize the use case with its Unit of Work factory."""

        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: CreateMeetingCommand) -> CreateMeetingResult:
        """Create a Meeting, persist it, and return its identity."""

        async with self._unit_of_work_factory() as unit_of_work:
            meeting = Meeting.create(name=command.name)
            await unit_of_work.meetings.save(meeting)
            await unit_of_work.commit()
            return CreateMeetingResult(meeting_id=meeting.id)
