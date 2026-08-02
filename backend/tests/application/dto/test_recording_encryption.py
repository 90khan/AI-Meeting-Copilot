"""Tests for redacted recording encryption boundary values."""

import pytest
from app.application.dto.recordings import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)
from app.application.exceptions import ApplicationValidationError


def test_recording_encryption_key_requires_exactly_32_bytes_and_is_redacted() -> None:
    key = RecordingEncryptionKey(_value=b"x" * 32)

    assert "x" * 32 not in repr(key)
    assert "x" * 32 not in str(key)
    with pytest.raises(ApplicationValidationError):
        RecordingEncryptionKey(_value=b"x" * 31)
    with pytest.raises(ApplicationValidationError):
        RecordingEncryptionKey(_value="x" * 32)  # type: ignore[arg-type]


def test_recording_key_reference_is_opaque_and_validated() -> None:
    reference = RecordingKeyReference(value="a" * 24)

    assert str(reference) == "a" * 24
    for value in ("", "short", "meeting name", "/tmp/recording"):
        with pytest.raises(ApplicationValidationError):
            RecordingKeyReference(value=value)
