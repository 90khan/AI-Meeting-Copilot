"""Focused tests for immutable recording metadata contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMetadata,
    RecordingRetentionPolicy,
    RecordingState,
)
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


def _metadata(**overrides: object) -> RecordingMetadata:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    values: dict[str, object] = {
        "recording_id": uuid4(),
        "meeting_id": MeetingId.new(),
        "state": RecordingState.PENDING,
        "retention_policy": RecordingRetentionPolicy.SEVEN_DAYS,
        "created_at": created_at,
        "expires_at": created_at + timedelta(days=7),
        "capture_anchor_utc": None,
        "duration_seconds": None,
        "protected": False,
        "consent_confirmed": False,
        "consent_confirmed_at": None,
        "deletion_status": RecordingDeletionStatus.NOT_SCHEDULED,
        "deleted_at": None,
        "encryption_format_version": 1,
        "container_format": "m4a",
        "segment_count": 0,
        "has_gaps": False,
    }
    values.update(overrides)
    return RecordingMetadata(**values)  # type: ignore[arg-type]


def test_retention_expiry_is_deterministic_and_utc() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    assert RecordingRetentionPolicy.ONE_DAY.expires_at(
        created_at
    ) == created_at + timedelta(days=1)
    assert RecordingRetentionPolicy.SEVEN_DAYS.expires_at(
        created_at
    ) == created_at + timedelta(days=7)
    assert RecordingRetentionPolicy.THIRTY_DAYS.expires_at(
        created_at
    ) == created_at + timedelta(days=30)
    assert RecordingRetentionPolicy.MANUAL.expires_at(created_at) is None


def test_recording_metadata_enforces_consent_retention_and_deletion() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ApplicationValidationError):
        _metadata(state=RecordingState.RECORDING)
    with pytest.raises(ApplicationValidationError):
        _metadata(retention_policy=RecordingRetentionPolicy.MANUAL)
    with pytest.raises(ApplicationValidationError):
        _metadata(state=RecordingState.DELETED)
    metadata = _metadata(
        state=RecordingState.DELETED,
        deletion_status=RecordingDeletionStatus.DELETED,
        deleted_at=created_at,
    )
    with pytest.raises(FrozenInstanceError):
        metadata.segment_count = 2  # type: ignore[misc]


def test_recording_metadata_rejects_non_utc_or_invalid_public_values() -> None:
    with pytest.raises(ApplicationValidationError):
        _metadata(created_at=datetime(2026, 1, 1))
    with pytest.raises(ApplicationValidationError):
        _metadata(duration_seconds=float("nan"))
    with pytest.raises(ApplicationValidationError):
        _metadata(container_format="wav")
    assert "path" not in RecordingMetadata.__dataclass_fields__
    assert "key" not in RecordingMetadata.__dataclass_fields__
