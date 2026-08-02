"""Tests for finalized live-transcription application DTOs."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan
from uuid import uuid4

import pytest
from app.application.dto import (
    AudioSource,
    CapturedAudioChunk,
    LiveTranscriptionChunkResult,
    LiveTranscriptionStatus,
    LiveTranscriptionStatusKind,
    ProcessedTranscriptSegment,
)
from app.application.dto.ai import AudioFormat, AudioInput
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


def _audio(*, sample_rate_hz: int = 16_000, channels: int = 1) -> AudioInput:
    return AudioInput(
        data=b"wav",
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        audio_format=AudioFormat.WAV,
    )


def _chunk(**overrides: object) -> CapturedAudioChunk:
    values: dict[str, object] = {
        "meeting_id": MeetingId.new(),
        "sequence": 0,
        "capture_started_at": datetime.now(UTC),
        "audio": _audio(),
        "source": AudioSource.MIXED,
    }
    values.update(overrides)
    return CapturedAudioChunk(**values)  # type: ignore[arg-type]


def test_audio_source_values_are_stable() -> None:
    """Audio sources expose the exact V1 wire values."""

    assert [source.value for source in AudioSource] == [
        "mixed",
        "microphone",
        "system_audio",
    ]


def test_live_transcription_status_kind_values_are_stable() -> None:
    """Status kinds expose the exact V1 lifecycle values."""

    assert [kind.value for kind in LiveTranscriptionStatusKind] == [
        "capture_active",
        "processing",
        "delayed",
        "gap",
        "permission_error",
        "device_error",
        "provider_error",
        "stopping",
        "stopped",
    ]


def test_captured_audio_chunk_is_immutable() -> None:
    """Capture DTOs cannot be changed after construction."""

    chunk = _chunk()

    with pytest.raises(FrozenInstanceError):
        chunk.sequence = 1  # type: ignore[misc]


def test_captured_audio_chunk_rejects_negative_sequence() -> None:
    """Capture chunks require non-negative sequences."""

    with pytest.raises(ApplicationValidationError, match="sequence"):
        _chunk(sequence=-1)


@pytest.mark.parametrize(
    "capture_started_at",
    [datetime.now(), datetime.now(UTC).astimezone(timezone(timedelta(hours=1)))],
)
def test_captured_audio_chunk_rejects_non_utc_timestamps(
    capture_started_at: datetime,
) -> None:
    """Capture timestamps must be timezone-aware UTC values."""

    with pytest.raises(ApplicationValidationError, match="timestamp"):
        _chunk(capture_started_at=capture_started_at)


@pytest.mark.parametrize(
    ("audio", "message"),
    [
        (_audio(sample_rate_hz=8_000), "16000"),
        (_audio(channels=2), "mono"),
    ],
)
def test_captured_audio_chunk_enforces_normalized_wav_shape(
    audio: AudioInput,
    message: str,
) -> None:
    """Live chunks require WAV, 16 kHz, mono audio metadata."""

    with pytest.raises(ApplicationValidationError, match=message):
        _chunk(audio=audio)


@pytest.mark.parametrize("overlap_seconds", [-0.1, inf, nan])
def test_captured_audio_chunk_rejects_invalid_overlap(overlap_seconds: float) -> None:
    """Overlap must be finite and non-negative."""

    with pytest.raises(ApplicationValidationError, match="overlap"):
        _chunk(overlap_seconds=overlap_seconds)


def test_status_validates_optional_message_and_chunk_sequence() -> None:
    """Status metadata rejects blank messages and negative chunk context."""

    with pytest.raises(ApplicationValidationError, match="message"):
        LiveTranscriptionStatus(kind=LiveTranscriptionStatusKind.DELAYED, message=" ")
    with pytest.raises(ApplicationValidationError, match="sequence"):
        LiveTranscriptionStatus(
            kind=LiveTranscriptionStatusKind.DELAYED,
            chunk_sequence=-1,
        )


@pytest.mark.parametrize(
    ("text", "timestamp", "speaker"),
    [
        ("", datetime.now(UTC), "Unknown"),
        ("Text", datetime.now(), "Unknown"),
        ("Text", datetime.now(UTC), ""),
    ],
)
def test_processed_transcript_segment_validates_content(
    text: str,
    timestamp: datetime,
    speaker: str,
) -> None:
    """Processed segments require non-blank text/speaker and UTC timestamps."""

    with pytest.raises(ApplicationValidationError):
        ProcessedTranscriptSegment(
            transcript_id=uuid4(),
            text=text,
            timestamp=timestamp,
            source=AudioSource.MIXED,
            speaker=speaker,
        )


def test_processed_transcript_segment_requires_a_uuid_identity() -> None:
    """Transcript identities must be explicit UUID instances."""

    with pytest.raises(ApplicationValidationError, match="Transcript ID"):
        ProcessedTranscriptSegment(  # type: ignore[arg-type]
            transcript_id="not-a-uuid",
            text="Text",
            timestamp=datetime.now(UTC),
            source=AudioSource.MIXED,
        )


def test_chunk_result_is_immutable_and_validates_sequence() -> None:
    """Chunk results are immutable and require non-negative sequences."""

    result = LiveTranscriptionChunkResult(
        chunk_sequence=0,
        accepted_segments=(),
        skipped_silence=True,
    )
    with pytest.raises(FrozenInstanceError):
        result.skipped_silence = False  # type: ignore[misc]
    with pytest.raises(ApplicationValidationError, match="sequence"):
        LiveTranscriptionChunkResult(
            chunk_sequence=-1,
            accepted_segments=(),
            skipped_silence=False,
        )
