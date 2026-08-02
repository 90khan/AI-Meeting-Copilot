"""Tests for finalizing local recording metadata."""

from dataclasses import replace
from math import nan
from uuid import UUID

import pytest
from app.application.dto.recordings import FinalizeRecordingCommand, RecordingState
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import FinalizeRecordingUseCase
from app.domain.entities import Meeting
from app.domain.exceptions import InvalidStateTransitionError

from .recording_fakes import FakeUnitOfWork, FakeUnitOfWorkFactory, recording_record


@pytest.mark.anyio
@pytest.mark.parametrize("state", [RecordingState.RECORDING, RecordingState.FINALIZING])
async def test_finalize_moves_valid_recording_to_completed_without_touching_meeting(
    state: RecordingState,
) -> None:
    meeting = Meeting.create(name="Review")
    record = recording_record(
        recording_id=UUID(int=1), meeting_id=meeting.id, state=state
    )
    unit_of_work = FakeUnitOfWork([meeting], [record])

    await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(unit_of_work)).execute(
        FinalizeRecordingCommand(
            recording_id=UUID(int=1),
            duration_seconds=12.5,
            segment_count=3,
            has_gaps=True,
        )
    )

    saved = unit_of_work.recordings.records[UUID(int=1)]
    assert saved.metadata.state is RecordingState.COMPLETED
    assert saved.metadata.duration_seconds == 12.5
    assert saved.metadata.segment_count == 3
    assert saved.metadata.has_gaps is True
    assert unit_of_work.meetings.meetings[meeting.id] is meeting
    assert meeting.transcripts == ()
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1


@pytest.mark.anyio
async def test_finalize_rejects_missing_invalid_and_malformed_metadata() -> None:
    command = FinalizeRecordingCommand(
        recording_id=UUID(int=1), duration_seconds=1.0, segment_count=1, has_gaps=False
    )
    missing = FakeUnitOfWork()
    with pytest.raises(LookupError, match="Recording not found"):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(missing)).execute(command)

    invalid = FakeUnitOfWork(
        records=[recording_record(recording_id=UUID(int=1), meeting_id=_meeting().id)]
    )
    with pytest.raises(InvalidStateTransitionError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(invalid)).execute(command)

    record = recording_record(
        recording_id=UUID(int=1),
        meeting_id=_meeting().id,
        state=RecordingState.RECORDING,
    )
    malformed = FakeUnitOfWork(records=[record])
    with pytest.raises(ApplicationValidationError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(malformed)).execute(
            replace(command, duration_seconds=nan)
        )
    with pytest.raises(ApplicationValidationError):
        await FinalizeRecordingUseCase(FakeUnitOfWorkFactory(malformed)).execute(
            replace(command, segment_count=-1)
        )

    for failed_unit_of_work in (missing, invalid, malformed):
        assert failed_unit_of_work.recordings.save_calls == 0
        assert failed_unit_of_work.commits == 0
        assert failed_unit_of_work.exited


def _meeting() -> Meeting:
    return Meeting.create(name="Review")
