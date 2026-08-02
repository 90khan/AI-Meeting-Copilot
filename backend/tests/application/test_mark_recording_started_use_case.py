"""Tests for starting prepared recording metadata."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.recordings import MarkRecordingStartedCommand, RecordingState
from app.application.use_cases import MarkRecordingStartedUseCase
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId

from .recording_fakes import FakeUnitOfWork, FakeUnitOfWorkFactory, recording_record


@pytest.mark.anyio
async def test_pending_consented_recording_starts_and_preserves_metadata() -> None:
    record = recording_record(recording_id=UUID(int=1), meeting_id=_meeting_id())
    unit_of_work = FakeUnitOfWork(records=[record])
    use_case = MarkRecordingStartedUseCase(FakeUnitOfWorkFactory(unit_of_work))
    anchor = datetime(2026, 1, 1, 12, tzinfo=UTC)

    await use_case.execute(
        MarkRecordingStartedCommand(recording_id=UUID(int=1), capture_anchor_utc=anchor)
    )

    saved = unit_of_work.recordings.records[UUID(int=1)]
    assert saved.metadata.state is RecordingState.RECORDING
    assert saved.metadata.capture_anchor_utc == anchor
    assert saved.storage_directory_token == record.storage_directory_token
    assert saved.key_reference == record.key_reference
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1
    assert unit_of_work.exited


@pytest.mark.anyio
async def test_start_rejects_missing_invalid_or_unconsented_recording() -> None:
    command = MarkRecordingStartedCommand(
        recording_id=UUID(int=1), capture_anchor_utc=datetime(2026, 1, 1, tzinfo=UTC)
    )
    missing = FakeUnitOfWork()
    with pytest.raises(LookupError, match="Recording not found"):
        await MarkRecordingStartedUseCase(FakeUnitOfWorkFactory(missing)).execute(
            command
        )

    invalid = FakeUnitOfWork(
        records=[
            recording_record(
                recording_id=UUID(int=1),
                meeting_id=_meeting_id(),
                state=RecordingState.RECORDING,
            )
        ]
    )
    with pytest.raises(InvalidStateTransitionError):
        await MarkRecordingStartedUseCase(FakeUnitOfWorkFactory(invalid)).execute(
            command
        )

    record = recording_record(recording_id=UUID(int=1), meeting_id=_meeting_id())
    unconsented = replace(
        record,
        metadata=replace(
            record.metadata,
            consent_confirmed=False,
            consent_confirmed_at=None,
        ),
    )
    unconsented_unit_of_work = FakeUnitOfWork(records=[unconsented])
    with pytest.raises(InvalidStateTransitionError):
        await MarkRecordingStartedUseCase(
            FakeUnitOfWorkFactory(unconsented_unit_of_work)
        ).execute(command)

    for failed_unit_of_work in (missing, invalid, unconsented_unit_of_work):
        assert failed_unit_of_work.recordings.save_calls == 0
        assert failed_unit_of_work.commits == 0
        assert failed_unit_of_work.exited


def _meeting_id() -> MeetingId:
    return MeetingId(UUID(int=2))
