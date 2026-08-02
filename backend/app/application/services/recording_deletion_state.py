"""Pure immutable transitions for local recording deletion metadata."""

from dataclasses import replace
from datetime import datetime, timedelta

from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMetadataRecord,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.domain.exceptions import InvalidStateTransitionError

_APPROVED_FAILURE_CODES = frozenset(
    {
        "storage_delete_failed",
        "key_delete_failed",
        "storage_missing",
        "key_missing",
        "reconciliation_failed",
    }
)
_ACTIVE_STATES = frozenset({RecordingState.RECORDING, RecordingState.FINALIZING})


def mark_deleting(record: RecordingMetadataRecord) -> RecordingMetadataRecord:
    """Mark an eligible record as awaiting external audio/key deletion."""

    _reject_active(record)
    if record.metadata.state is RecordingState.DELETED:
        return record
    return replace(
        record,
        metadata=replace(
            record.metadata,
            deletion_status=RecordingDeletionStatus.DELETING,
        ),
    )


def mark_deleted(
    record: RecordingMetadataRecord,
    *,
    deleted_at: datetime,
) -> RecordingMetadataRecord:
    """Record fully completed external cleanup without touching Meeting data."""

    _require_utc(deleted_at)
    _reject_active(record)
    metadata = record.metadata
    if metadata.state is RecordingState.DELETED and metadata.deleted_at == deleted_at:
        return record
    return replace(
        record,
        metadata=replace(
            metadata,
            state=RecordingState.DELETED,
            deletion_status=RecordingDeletionStatus.DELETED,
            deleted_at=deleted_at,
            expires_at=None,
            protected=False,
        ),
        failure_code=None,
    )


def mark_deletion_failed(
    record: RecordingMetadataRecord,
    *,
    failure_code: str,
) -> RecordingMetadataRecord:
    """Record a closed-set, privacy-safe external deletion failure."""

    if failure_code not in _APPROVED_FAILURE_CODES:
        raise ApplicationValidationError("Recording deletion failure code is invalid.")
    if record.metadata.state is RecordingState.DELETED:
        raise InvalidStateTransitionError("Deleted recordings cannot fail deletion.")
    return replace(
        record,
        metadata=replace(
            record.metadata,
            deletion_status=RecordingDeletionStatus.FAILED,
        ),
        failure_code=failure_code,
    )


def mark_storage_missing(record: RecordingMetadataRecord) -> RecordingMetadataRecord:
    """Record missing encrypted storage while retaining metadata for reconciliation."""

    _reject_active(record)
    if record.metadata.state is RecordingState.DELETED:
        return record
    return replace(
        record,
        metadata=replace(
            record.metadata,
            state=RecordingState.MISSING,
            deletion_status=RecordingDeletionStatus.FAILED,
        ),
        failure_code="storage_missing",
    )


def _reject_active(record: RecordingMetadataRecord) -> None:
    if record.metadata.state in _ACTIVE_STATES:
        raise InvalidStateTransitionError("Active recordings cannot be deleted.")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ApplicationValidationError("Recording timestamps must use UTC.")
