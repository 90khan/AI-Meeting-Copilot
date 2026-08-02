"""Use case for validating a live-transcription session start."""

from app.application.dto import (
    StartLiveTranscriptionSessionCommand,
    StartLiveTranscriptionSessionResult,
)
from app.application.interfaces import UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingStatus


class StartLiveTranscriptionSessionUseCase:
    """Validate that a Meeting can begin live transcription without mutation."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """Initialize the use case with its Unit of Work factory."""

        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        command: StartLiveTranscriptionSessionCommand,
    ) -> StartLiveTranscriptionSessionResult:
        """Validate an active Meeting and return its accepted session configuration."""

        async with self._unit_of_work_factory() as unit_of_work:
            meeting = await unit_of_work.meetings.get_by_id(command.meeting_id)
            if meeting is None:
                raise LookupError("Meeting not found")
            if meeting.status is not MeetingStatus.ACTIVE:
                raise InvalidStateTransitionError(
                    "Live transcription can start only for an active Meeting."
                )

            return StartLiveTranscriptionSessionResult(
                meeting_id=command.meeting_id,
                language_hint=command.language_hint,
                source=command.source,
            )
