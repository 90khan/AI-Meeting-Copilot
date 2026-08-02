"""Use case for adding one finalized transcript entry to a Meeting."""

from app.application.dto import AddTranscriptCommand, AddTranscriptResult
from app.application.interfaces import UnitOfWorkFactory


class AddTranscriptUseCase:
    """Add and persist one transcript entry on an existing Meeting."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Initialize the use case with its Unit of Work factory."""

        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: AddTranscriptCommand) -> AddTranscriptResult:
        """Add a transcript entry, persist its Meeting, and return its identity."""

        async with self._unit_of_work_factory() as unit_of_work:
            meeting = await unit_of_work.meetings.get_by_id(command.meeting_id)
            if meeting is None:
                raise LookupError("Meeting not found")

            transcript = meeting.add_transcript(
                speaker=command.speaker,
                text=command.text,
                timestamp=command.timestamp,
            )
            await unit_of_work.meetings.save(meeting)
            await unit_of_work.commit()
            return AddTranscriptResult(transcript_id=transcript.id)
