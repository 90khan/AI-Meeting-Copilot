"""Tests for private immutable recording playback DTOs."""

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingMediaFormat,
    RecordingPlaybackInfo,
    RecordingPlaybackSegment,
)
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


def test_playback_info_is_private_safe_and_immutable() -> None:
    info = RecordingPlaybackInfo(
        recording_id=UUID(int=1),
        meeting_id=MeetingId(UUID(int=2)),
        format=RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1,
        duration_seconds=4.0,
        segment_count=2,
        has_gaps=True,
    )
    assert "token" not in info.__dataclass_fields__
    assert "key" not in info.__dataclass_fields__
    with pytest.raises(FrozenInstanceError):
        info.segment_count = 3  # type: ignore[misc]


def test_playback_dtos_reject_invalid_values_and_redact_audio_repr() -> None:
    with pytest.raises(ApplicationValidationError):
        RecordingPlaybackInfo(
            recording_id=UUID(int=1),
            meeting_id=MeetingId(UUID(int=2)),
            format=RecordingMediaFormat.LEGACY_M4A,
            duration_seconds=None,
            segment_count=0,
            has_gaps=False,
        )
    with pytest.raises(ApplicationValidationError):
        RecordingPlaybackSegment(segment_index=-1, plaintext_audio=b"audio")
    segment = RecordingPlaybackSegment(
        segment_index=0,
        plaintext_audio=b"private audio",
    )
    assert "private audio" not in repr(segment)
    with pytest.raises(FrozenInstanceError):
        segment.plaintext_audio = b"changed"  # type: ignore[misc]
