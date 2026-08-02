"""Tests for privacy-safe recording failure metadata."""

from uuid import UUID

import pytest
from app.application.dto.recordings import MarkRecordingFailedCommand, RecordingState
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import MarkRecordingFailedUseCase
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId

from .recording_fakes import FakeUnitOfWork, FakeUnitOfWorkFactory, recording_record


@pytest.mark.anyio
async def test_failure_transitions_recording_and_same_code_is_idempotent() -> None:
    record = recording_record(
        recording_id=UUID(int=1), meeting_id=MeetingId(UUID(int=2))
    )
    unit_of_work = FakeUnitOfWork(records=[record])
    use_case = MarkRecordingFailedUseCase(FakeUnitOfWorkFactory(unit_of_work))
    command = MarkRecordingFailedCommand(
        recording_id=UUID(int=1), failure_code="writer_failed"
    )

    await use_case.execute(command)
    saved = unit_of_work.recordings.records[UUID(int=1)]
    assert saved.metadata.state is RecordingState.FAILED
    assert saved.failure_code == "writer_failed"
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1

    await use_case.execute(command)
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1


@pytest.mark.anyio
async def test_failure_rejects_missing_and_deleted_recordings() -> None:
    command = MarkRecordingFailedCommand(
        recording_id=UUID(int=1), failure_code="writer_failed"
    )
    missing = FakeUnitOfWork()
    with pytest.raises(LookupError, match="Recording not found"):
        await MarkRecordingFailedUseCase(FakeUnitOfWorkFactory(missing)).execute(
            command
        )

    deleted = FakeUnitOfWork(
        records=[
            recording_record(
                recording_id=UUID(int=1),
                meeting_id=MeetingId(UUID(int=2)),
                state=RecordingState.DELETED,
            )
        ]
    )
    with pytest.raises(InvalidStateTransitionError):
        await MarkRecordingFailedUseCase(FakeUnitOfWorkFactory(deleted)).execute(
            command
        )

    for unit_of_work in (missing, deleted):
        assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0


@pytest.mark.parametrize(
    "failure_code",
    [
        "",
        "unknown-code",
        "unknown_code",
        "/tmp/path",
        "device_error",
        "exception_trace",
        "transcript_text",
        "secret_token",
    ],
)
def test_failure_code_rejects_non_allowlisted_or_sensitive_text(
    failure_code: str,
) -> None:
    with pytest.raises(ApplicationValidationError):
        MarkRecordingFailedCommand(recording_id=UUID(int=1), failure_code=failure_code)
