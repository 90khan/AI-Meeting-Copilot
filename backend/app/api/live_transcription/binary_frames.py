"""Binary WAV frame serialization for the live-transcription protocol."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.api.live_transcription.protocol import (
    AUDIO_FRAME_MAGIC,
    AUDIO_MESSAGE_KIND,
    DEFAULT_MAX_BINARY_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
)
from app.application.dto.ai import AudioFormat
from app.application.dto.live_transcription import AudioSource
from app.application.exceptions import ApplicationValidationError

_FRAME_HEADER_LENGTH = 8


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioChunkFrameMetadata:
    """Validated metadata preceding one self-describing WAV payload."""

    session_id: UUID
    sequence: int
    capture_started_at: datetime
    source: AudioSource
    audio_format: AudioFormat
    sample_rate_hz: int
    channels: int
    overlap_seconds: float
    byte_length: int

    def __post_init__(self) -> None:
        """Validate normalized V1 live-audio frame metadata."""

        if not isinstance(self.session_id, UUID):
            raise ApplicationValidationError("Audio frame session ID is invalid.")
        _validate_non_negative_integer(self.sequence, "Audio frame sequence")
        _validate_utc_timestamp(self.capture_started_at)
        if not isinstance(self.source, AudioSource):
            raise ApplicationValidationError("Audio frame source is invalid.")
        if self.audio_format is not AudioFormat.WAV:
            raise ApplicationValidationError("Audio frames must use WAV audio.")
        if self.sample_rate_hz != 16_000:
            raise ApplicationValidationError(
                "Audio frames must use a 16000 Hz sample rate."
            )
        if self.channels != 1:
            raise ApplicationValidationError("Audio frames must be mono.")
        if (
            not isinstance(self.overlap_seconds, (int, float))
            or isinstance(self.overlap_seconds, bool)
            or not math.isfinite(self.overlap_seconds)
            or self.overlap_seconds < 0
        ):
            raise ApplicationValidationError(
                "Audio frame overlap must be a non-negative finite value."
            )
        _validate_positive_integer(self.byte_length, "Audio frame byte length")


def build_audio_chunk_frame(
    *, metadata: AudioChunkFrameMetadata, wav_payload: bytes
) -> bytes:
    """Build one compact binary frame containing metadata and WAV bytes."""

    if not isinstance(metadata, AudioChunkFrameMetadata):
        raise ApplicationValidationError("Audio frame metadata is invalid.")
    if not isinstance(wav_payload, bytes):
        raise ApplicationValidationError("Audio frame payload is invalid.")
    if metadata.byte_length != len(wav_payload):
        raise ApplicationValidationError(
            "Audio frame payload length does not match metadata."
        )

    metadata_bytes = json.dumps(
        _metadata_to_payload(metadata), separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(metadata_bytes) > 65_535:
        raise ApplicationValidationError("Audio frame metadata is too large.")

    return b"".join(
        (
            AUDIO_FRAME_MAGIC,
            bytes((PROTOCOL_VERSION, AUDIO_MESSAGE_KIND)),
            len(metadata_bytes).to_bytes(2, byteorder="big"),
            metadata_bytes,
            wav_payload,
        )
    )


def parse_audio_chunk_frame(
    frame: bytes,
    *,
    max_payload_bytes: int = DEFAULT_MAX_BINARY_PAYLOAD_BYTES,
) -> tuple[AudioChunkFrameMetadata, bytes]:
    """Parse and strictly validate one transport-neutral binary audio frame."""

    _validate_positive_integer(max_payload_bytes, "Maximum binary payload size")
    if not isinstance(frame, bytes) or len(frame) < _FRAME_HEADER_LENGTH:
        raise ApplicationValidationError("Invalid audio frame.")
    if frame[:4] != AUDIO_FRAME_MAGIC:
        raise ApplicationValidationError("Invalid audio frame.")
    if frame[4] != PROTOCOL_VERSION:
        raise ApplicationValidationError("Unsupported audio frame version.")
    if frame[5] != AUDIO_MESSAGE_KIND:
        raise ApplicationValidationError("Unsupported audio frame kind.")

    metadata_length = int.from_bytes(frame[6:8], byteorder="big")
    metadata_end = _FRAME_HEADER_LENGTH + metadata_length
    if metadata_end > len(frame):
        raise ApplicationValidationError("Invalid audio frame metadata.")

    try:
        metadata_json = frame[_FRAME_HEADER_LENGTH:metadata_end].decode("utf-8")
        metadata_payload = json.loads(metadata_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationValidationError("Invalid audio frame metadata.") from error
    if not isinstance(metadata_payload, dict):
        raise ApplicationValidationError("Invalid audio frame metadata.")

    metadata = _metadata_from_payload(metadata_payload)
    wav_payload = frame[metadata_end:]
    if len(wav_payload) > max_payload_bytes:
        raise ApplicationValidationError(
            "Audio frame payload exceeds the permitted size."
        )
    if metadata.byte_length != len(wav_payload):
        raise ApplicationValidationError(
            "Audio frame payload length does not match metadata."
        )
    return metadata, wav_payload


def _metadata_to_payload(metadata: AudioChunkFrameMetadata) -> dict[str, object]:
    """Convert validated metadata into the binary frame's JSON object."""

    timestamp = (
        metadata.capture_started_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    return {
        "audio_format": metadata.audio_format.value,
        "byte_length": metadata.byte_length,
        "capture_started_at": timestamp,
        "channels": metadata.channels,
        "overlap_seconds": metadata.overlap_seconds,
        "sample_rate_hz": metadata.sample_rate_hz,
        "sequence": metadata.sequence,
        "session_id": str(metadata.session_id),
        "source": metadata.source.value,
    }


def _metadata_from_payload(payload: dict[object, object]) -> AudioChunkFrameMetadata:
    """Construct metadata from an exact primitive JSON schema."""

    expected_fields = {
        "session_id",
        "sequence",
        "capture_started_at",
        "source",
        "audio_format",
        "sample_rate_hz",
        "channels",
        "overlap_seconds",
        "byte_length",
    }
    if set(payload) != expected_fields:
        raise ApplicationValidationError("Invalid audio frame metadata.")
    try:
        session_id = UUID(_require_string(payload, "session_id"))
        capture_started_at = _parse_timestamp(
            _require_string(payload, "capture_started_at")
        )
        source = AudioSource(_require_string(payload, "source"))
        audio_format = AudioFormat(_require_string(payload, "audio_format"))
        return AudioChunkFrameMetadata(
            session_id=session_id,
            sequence=_require_integer(payload, "sequence"),
            capture_started_at=capture_started_at,
            source=source,
            audio_format=audio_format,
            sample_rate_hz=_require_integer(payload, "sample_rate_hz"),
            channels=_require_integer(payload, "channels"),
            overlap_seconds=_require_number(payload, "overlap_seconds"),
            byte_length=_require_integer(payload, "byte_length"),
        )
    except (TypeError, ValueError) as error:
        raise ApplicationValidationError("Invalid audio frame metadata.") from error


def _parse_timestamp(value: str) -> datetime:
    """Parse ISO-8601 timestamps while accepting the canonical Z suffix."""

    normalized_value = (
        value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    )
    return datetime.fromisoformat(normalized_value)


def _require_string(payload: dict[object, object], field_name: str) -> str:
    value = payload[field_name]
    if not isinstance(value, str):
        raise ApplicationValidationError("Invalid audio frame metadata.")
    return value


def _require_integer(payload: dict[object, object], field_name: str) -> int:
    value = payload[field_name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApplicationValidationError("Invalid audio frame metadata.")
    return value


def _require_number(payload: dict[object, object], field_name: str) -> float:
    value = payload[field_name]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ApplicationValidationError("Invalid audio frame metadata.")
    return float(value)


def _validate_utc_timestamp(timestamp: datetime) -> None:
    if not isinstance(timestamp, datetime):
        raise ApplicationValidationError("Audio frame timestamp is invalid.")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ApplicationValidationError(
            "Audio frame timestamp must be timezone-aware."
        )
    if timestamp.utcoffset() != timedelta(0):
        raise ApplicationValidationError("Audio frame timestamp must use UTC.")


def _validate_non_negative_integer(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ApplicationValidationError(
            f"{field_name} must be a non-negative integer."
        )


def _validate_positive_integer(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ApplicationValidationError(f"{field_name} must be a positive integer.")
