"""Tests for pure recording deletion-state transitions."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.recordings import RecordingDeletionStatus, RecordingState
from app.application.exceptions import ApplicationValidationError
from app.application.services import (
    mark_deleted,
    mark_deleting,
    mark_deletion_failed,
    mark_storage_missing,
)
from app.domain.exceptions import InvalidStateTransitionError
from app.domain.value_objects import MeetingId

from ..recording_fakes import recording_record


def _record(state: RecordingState = RecordingState.PENDING):
    return recording_record(
        recording_id=UUID(int=1),
        meeting_id=MeetingId(UUID(int=2)),
        state=state,
        failure_code="writer_failed",
    )


def test_mark_deleting_preserves_state_and_rejects_active() -> None:
    pending = _record()
    completed = _record(RecordingState.COMPLETED)
    assert (
        mark_deleting(pending).metadata.deletion_status
        is RecordingDeletionStatus.DELETING
    )
    assert mark_deleting(completed).metadata.state is RecordingState.COMPLETED
    assert (
        mark_deleting(_record(RecordingState.DELETED)).metadata.state
        is RecordingState.DELETED
    )
    for state in (RecordingState.RECORDING, RecordingState.FINALIZING):
        with pytest.raises(InvalidStateTransitionError):
            mark_deleting(_record(state))


def test_mark_deleted_sets_final_fields_and_is_idempotent() -> None:
    record = _record(RecordingState.COMPLETED)
    deleted_at = datetime(2026, 1, 2, tzinfo=UTC)
    result = mark_deleted(record, deleted_at=deleted_at)
    assert result.metadata.state is RecordingState.DELETED
    assert result.metadata.deletion_status is RecordingDeletionStatus.DELETED
    assert result.metadata.deleted_at == deleted_at
    assert result.metadata.expires_at is None
    assert result.metadata.protected is False
    assert result.failure_code is None
    assert result.metadata.duration_seconds == record.metadata.duration_seconds
    assert mark_deleted(result, deleted_at=deleted_at) is result
    with pytest.raises(ApplicationValidationError):
        mark_deleted(record, deleted_at=datetime(2026, 1, 2))


def test_failure_and_missing_transitions_are_closed_and_immutable() -> None:
    record = _record()
    for code in (
        "storage_delete_failed",
        "key_delete_failed",
        "storage_missing",
        "key_missing",
        "reconciliation_failed",
    ):
        assert mark_deletion_failed(record, failure_code=code).failure_code == code
    with pytest.raises(ApplicationValidationError):
        mark_deletion_failed(record, failure_code="unknown")
    with pytest.raises(InvalidStateTransitionError):
        mark_deletion_failed(
            _record(RecordingState.DELETED), failure_code="key_missing"
        )
    missing = mark_storage_missing(record)
    assert missing.metadata.state is RecordingState.MISSING
    assert missing.failure_code == "storage_missing"
    assert record.metadata.state is RecordingState.PENDING
    with pytest.raises(InvalidStateTransitionError):
        mark_storage_missing(_record(RecordingState.RECORDING))
