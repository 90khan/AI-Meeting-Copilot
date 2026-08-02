"""Tests for safe recording storage application contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.dto.recordings import RecordingSegmentDescriptor
from app.application.exceptions import (
    RecordingSegmentAuthenticationError,
    RecordingStorageError,
)
from app.application.exceptions.validation import ApplicationValidationError


def test_descriptor_is_immutable_and_validated() -> None:
    descriptor = RecordingSegmentDescriptor(
        recording_id=uuid4(),
        segment_index=0,
        plaintext_length=1,
        ciphertext_length=17,
        created_at=datetime.now(UTC),
        completed=True,
        format_version=1,
    )
    with pytest.raises(AttributeError):
        descriptor.segment_index = 1  # type: ignore[misc]
    with pytest.raises(ApplicationValidationError):
        RecordingSegmentDescriptor(
            recording_id=uuid4(),
            segment_index=-1,
            plaintext_length=0,
            ciphertext_length=0,
            created_at=datetime.now(UTC),
            completed=True,
            format_version=1,
        )


def test_storage_errors_have_redacted_stable_defaults() -> None:
    assert str(RecordingStorageError()) == "Recording storage operation failed."
    assert "key" not in str(RecordingSegmentAuthenticationError()).lower()
