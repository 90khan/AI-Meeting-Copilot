"""Tests for strict binary WAV-frame serialization and parsing."""

import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from app.api.live_transcription.binary_frames import (
    AudioChunkFrameMetadata,
    build_audio_chunk_frame,
    parse_audio_chunk_frame,
)
from app.api.live_transcription.protocol import (
    AUDIO_FRAME_MAGIC,
    AUDIO_MESSAGE_KIND,
)
from app.application.dto.ai import AudioFormat
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError

_SESSION_ID = UUID("66666666-6666-6666-6666-666666666666")
_TIMESTAMP = datetime(2026, 8, 2, 12, 30, 45, 123_000, tzinfo=UTC)
_PAYLOAD = b"RIFF-payload"


def _metadata(
    *, byte_length: int = len(_PAYLOAD), upstream_pending_chunks: int = 0
) -> AudioChunkFrameMetadata:
    return AudioChunkFrameMetadata(
        session_id=_SESSION_ID,
        sequence=7,
        capture_started_at=_TIMESTAMP,
        source=AudioSource.MIXED,
        audio_format=AudioFormat.WAV,
        sample_rate_hz=16_000,
        channels=1,
        overlap_seconds=0.5,
        upstream_pending_chunks=upstream_pending_chunks,
        byte_length=byte_length,
    )


def _raw_frame(metadata: dict[str, object], payload: bytes) -> bytes:
    metadata_bytes = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    return b"".join(
        (
            AUDIO_FRAME_MAGIC,
            bytes((1, AUDIO_MESSAGE_KIND)),
            len(metadata_bytes).to_bytes(2, "big"),
            metadata_bytes,
            payload,
        )
    )


def test_binary_frame_round_trip_uses_compact_deterministic_metadata() -> None:
    """The serializer creates a compact inverse of the strict parser layout."""

    metadata = _metadata()
    frame = build_audio_chunk_frame(metadata=metadata, wav_payload=_PAYLOAD)
    metadata_length = int.from_bytes(frame[6:8], "big")
    metadata_bytes = frame[8 : 8 + metadata_length]

    assert frame == build_audio_chunk_frame(metadata=metadata, wav_payload=_PAYLOAD)
    assert parse_audio_chunk_frame(frame) == (metadata, _PAYLOAD)
    assert b" " not in metadata_bytes
    assert json.loads(metadata_bytes)["capture_started_at"].endswith("Z")


def test_binary_frame_carries_only_a_non_negative_structural_backlog_count() -> None:
    """The admission signal is bounded metadata, never retained audio content."""

    metadata = _metadata(upstream_pending_chunks=3)
    frame = build_audio_chunk_frame(metadata=metadata, wav_payload=_PAYLOAD)

    parsed_metadata, _ = parse_audio_chunk_frame(frame)
    assert parsed_metadata.upstream_pending_chunks == 3

    for invalid_pending_chunks in (-1, 256):
        with pytest.raises(ApplicationValidationError):
            AudioChunkFrameMetadata(
                session_id=_SESSION_ID,
                sequence=0,
                capture_started_at=_TIMESTAMP,
                source=AudioSource.MIXED,
                audio_format=AudioFormat.WAV,
                sample_rate_hz=16_000,
                channels=1,
                overlap_seconds=0.5,
                upstream_pending_chunks=invalid_pending_chunks,
                byte_length=1,
            )


@pytest.mark.parametrize(
    ("index", "value"),
    [(0, ord("X")), (4, 2), (5, 2)],
)
def test_invalid_magic_version_and_kind_are_rejected(index: int, value: int) -> None:
    """Fixed header fields protect the frame schema before JSON is parsed."""

    frame = bytearray(
        build_audio_chunk_frame(metadata=_metadata(), wav_payload=_PAYLOAD)
    )
    frame[index] = value

    with pytest.raises(ApplicationValidationError):
        parse_audio_chunk_frame(bytes(frame))


def test_invalid_metadata_length_is_rejected() -> None:
    """Metadata cannot claim bytes that are absent from the frame."""

    frame = bytearray(
        build_audio_chunk_frame(metadata=_metadata(), wav_payload=_PAYLOAD)
    )
    frame[6:8] = (65_535).to_bytes(2, "big")

    with pytest.raises(ApplicationValidationError, match="metadata"):
        parse_audio_chunk_frame(bytes(frame))


@pytest.mark.parametrize("metadata_bytes", [b"\xff", b"{"])
def test_invalid_utf8_and_json_metadata_are_rejected(metadata_bytes: bytes) -> None:
    """The metadata region must be a UTF-8 JSON object."""

    frame = b"".join(
        (
            AUDIO_FRAME_MAGIC,
            bytes((1, AUDIO_MESSAGE_KIND)),
            len(metadata_bytes).to_bytes(2, "big"),
            metadata_bytes,
            _PAYLOAD,
        )
    )

    with pytest.raises(ApplicationValidationError, match="metadata"):
        parse_audio_chunk_frame(frame)


def test_incorrect_declared_payload_length_is_rejected() -> None:
    """The payload bytes must exactly match the metadata declaration."""

    metadata = {
        "session_id": str(_SESSION_ID),
        "sequence": 0,
        "capture_started_at": "2026-08-02T12:30:45Z",
        "source": "mixed",
        "audio_format": "wav",
        "sample_rate_hz": 16_000,
        "channels": 1,
        "overlap_seconds": 0.5,
        "upstream_pending_chunks": 0,
        "byte_length": len(_PAYLOAD) + 1,
    }

    with pytest.raises(ApplicationValidationError, match="length"):
        parse_audio_chunk_frame(_raw_frame(metadata, _PAYLOAD))


def test_oversized_payload_is_rejected() -> None:
    """The parser enforces the caller's transport payload limit."""

    frame = build_audio_chunk_frame(metadata=_metadata(), wav_payload=_PAYLOAD)

    with pytest.raises(ApplicationValidationError, match="exceeds"):
        parse_audio_chunk_frame(frame, max_payload_bytes=len(_PAYLOAD) - 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "not-a-uuid"),
        ("capture_started_at", "not-a-timestamp"),
        ("audio_format", "mp3"),
        ("sample_rate_hz", 44_100),
        ("channels", 2),
    ],
)
def test_invalid_audio_metadata_is_rejected(field: str, value: object) -> None:
    """Transport metadata must conform to the normalized V1 audio contract."""

    payload = {
        "session_id": str(_SESSION_ID),
        "sequence": 0,
        "capture_started_at": "2026-08-02T12:30:45Z",
        "source": "mixed",
        "audio_format": "wav",
        "sample_rate_hz": 16_000,
        "channels": 1,
        "overlap_seconds": 0.5,
        "upstream_pending_chunks": 0,
        "byte_length": len(_PAYLOAD),
    }
    payload[field] = value

    with pytest.raises(ApplicationValidationError):
        parse_audio_chunk_frame(_raw_frame(payload, _PAYLOAD))


def test_non_utc_metadata_timestamp_is_rejected() -> None:
    """Frame metadata accepts only UTC-aware capture timestamps."""

    with pytest.raises(ApplicationValidationError, match="UTC"):
        AudioChunkFrameMetadata(
            session_id=_SESSION_ID,
            sequence=0,
            capture_started_at=_TIMESTAMP.astimezone(timezone(timedelta(hours=1))),
            source=AudioSource.MIXED,
            audio_format=AudioFormat.WAV,
            sample_rate_hz=16_000,
            channels=1,
            overlap_seconds=0.5,
            upstream_pending_chunks=0,
            byte_length=1,
        )


def test_serializer_requires_matching_payload_length() -> None:
    """A serializer cannot create a frame with ambiguous trailing bytes."""

    with pytest.raises(ApplicationValidationError, match="length"):
        build_audio_chunk_frame(metadata=_metadata(byte_length=1), wav_payload=_PAYLOAD)


def test_audio_bytes_never_appear_in_parser_errors() -> None:
    """Failures remain privacy-safe even when input contains sensitive bytes."""

    sensitive_payload = b"SECRET_AUDIO_BYTES"
    frame = bytearray(
        build_audio_chunk_frame(
            metadata=_metadata(byte_length=len(sensitive_payload)),
            wav_payload=sensitive_payload,
        )
    )
    frame[0] = ord("X")

    with pytest.raises(ApplicationValidationError) as error:
        parse_audio_chunk_frame(bytes(frame))

    assert sensitive_payload.decode("ascii") not in str(error.value)
