"""Tests for recording preparation orchestration."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    PrepareRecordingSessionCommand,
    RecordingDeletionStatus,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import PrepareRecordingSessionUseCase
from app.domain.entities import Meeting
from app.domain.exceptions import InvalidStateTransitionError

from .recording_fakes import (
    FakeRecordingKeyStore,
    FakeUnitOfWork,
    FakeUnitOfWorkFactory,
    recording_record,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _use_case(
    unit_of_work: FakeUnitOfWork,
    uuid_factory: object = lambda: UUID(int=1),
) -> PrepareRecordingSessionUseCase:
    return PrepareRecordingSessionUseCase(
        FakeUnitOfWorkFactory(unit_of_work),
        FakeRecordingKeyStore(),
        lambda: NOW,
        uuid_factory,  # type: ignore[arg-type]
    )


def _command(meeting: Meeting, **overrides: object) -> PrepareRecordingSessionCommand:
    values: dict[str, object] = {
        "meeting_id": meeting.id,
        "recording_enabled": True,
        "retention_policy": RecordingRetentionPolicy.SEVEN_DAYS,
        "consent_confirmed": True,
        "consent_confirmed_at": NOW,
    }
    values.update(overrides)
    return PrepareRecordingSessionCommand(**values)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_disabled_recording_has_no_side_effects() -> None:
    meeting = Meeting.create(name="Interview")
    unit_of_work = FakeUnitOfWork([meeting])
    factory = FakeUnitOfWorkFactory(unit_of_work)
    use_case = PrepareRecordingSessionUseCase(
        factory, FakeRecordingKeyStore(), lambda: NOW, lambda: UUID(int=1)
    )

    result = await use_case.execute(
        _command(
            meeting,
            recording_enabled=False,
            consent_confirmed=False,
            consent_confirmed_at=None,
        )
    )

    assert result.enabled is False
    assert not unit_of_work.recordings.records
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0
    assert factory.calls == 0
    assert not unit_of_work.exited


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("policy", "expiry"),
    [
        (RecordingRetentionPolicy.ONE_DAY, NOW + timedelta(days=1)),
        (RecordingRetentionPolicy.SEVEN_DAYS, NOW + timedelta(days=7)),
        (RecordingRetentionPolicy.THIRTY_DAYS, NOW + timedelta(days=30)),
        (RecordingRetentionPolicy.MANUAL, None),
    ],
)
async def test_active_meeting_prepares_opaque_pending_metadata(
    policy: RecordingRetentionPolicy, expiry: datetime | None
) -> None:
    meeting = Meeting.create(name="Interview")
    meeting.start()
    unit_of_work = FakeUnitOfWork([meeting])
    use_case = _use_case(unit_of_work)

    result = await use_case.execute(_command(meeting, retention_policy=policy))

    record = unit_of_work.recordings.records[UUID(int=1)]
    assert result.expires_at == expiry
    assert record.metadata.recording_id == UUID(int=1)
    assert record.metadata.state is RecordingState.PENDING
    assert record.metadata.deletion_status is (
        RecordingDeletionStatus.NOT_SCHEDULED
        if expiry is None
        else RecordingDeletionStatus.SCHEDULED
    )
    assert record.storage_directory_token
    assert record.key_reference == "a" * 24
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1
    assert unit_of_work.exited


@pytest.mark.anyio
async def test_missing_or_inactive_meeting_does_not_commit() -> None:
    draft = Meeting.create(name="Draft")
    ended = Meeting.create(name="Ended")
    ended.start()
    ended.end()
    for meeting in (draft, ended):
        unit_of_work = FakeUnitOfWork([meeting])
        use_case = _use_case(unit_of_work, UUID)
        with pytest.raises(InvalidStateTransitionError):
            await use_case.execute(_command(meeting))
        assert unit_of_work.commits == 0
        assert unit_of_work.exited
    unit_of_work = FakeUnitOfWork()
    use_case = _use_case(unit_of_work, UUID)
    with pytest.raises(LookupError, match="Meeting not found"):
        await use_case.execute(_command(draft))
    assert unit_of_work.commits == 0
    assert unit_of_work.exited


@pytest.mark.anyio
async def test_existing_deleted_recording_permits_new_recording() -> None:
    meeting = Meeting.create(name="Interview")
    meeting.start()
    deleted_record = recording_record(
        recording_id=UUID(int=11),
        meeting_id=meeting.id,
        state=RecordingState.DELETED,
    )
    unit_of_work = FakeUnitOfWork([meeting], [deleted_record])
    use_case = _use_case(unit_of_work, lambda: UUID(int=12))

    result = await use_case.execute(_command(meeting))

    assert result.recording_id == UUID(int=12)
    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 1


@pytest.mark.anyio
async def test_existing_non_deleted_recording_rejects_without_commit() -> None:
    meeting = Meeting.create(name="Interview")
    meeting.start()
    unit_of_work = FakeUnitOfWork(
        [meeting],
        [recording_record(recording_id=UUID(int=11), meeting_id=meeting.id)],
    )
    use_case = _use_case(unit_of_work, lambda: UUID(int=12))

    with pytest.raises(InvalidStateTransitionError):
        await use_case.execute(_command(meeting))

    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0
    assert unit_of_work.exited


@pytest.mark.anyio
async def test_repository_failure_exits_without_commit() -> None:
    meeting = Meeting.create(name="Interview")
    meeting.start()
    unit_of_work = FakeUnitOfWork(
        [meeting], recording_save_error=RuntimeError("persistence unavailable")
    )
    key_store = FakeRecordingKeyStore()
    use_case = PrepareRecordingSessionUseCase(
        FakeUnitOfWorkFactory(unit_of_work), key_store, lambda: NOW, lambda: UUID(int=1)
    )

    with pytest.raises(RuntimeError, match="persistence unavailable"):
        await use_case.execute(_command(meeting))

    assert unit_of_work.recordings.save_calls == 1
    assert unit_of_work.commits == 0
    assert unit_of_work.exited
    assert len(key_store.deleted) == 1


@pytest.mark.anyio
async def test_key_store_failure_creates_no_metadata_or_commit() -> None:
    meeting = Meeting.create(name="Interview")
    meeting.start()
    unit_of_work = FakeUnitOfWork([meeting])
    use_case = PrepareRecordingSessionUseCase(
        FakeUnitOfWorkFactory(unit_of_work),
        FakeRecordingKeyStore(RuntimeError("key store unavailable")),
        lambda: NOW,
        lambda: UUID(int=1),
    )

    with pytest.raises(RuntimeError, match="key store unavailable"):
        await use_case.execute(_command(meeting))

    assert unit_of_work.recordings.save_calls == unit_of_work.commits == 0


def test_prepare_command_requires_utc_confirmed_consent() -> None:
    meeting = Meeting.create(name="Interview")
    with pytest.raises(ApplicationValidationError):
        _command(meeting, consent_confirmed=False)
    with pytest.raises(ApplicationValidationError):
        _command(meeting, consent_confirmed_at=datetime(2026, 1, 1))
