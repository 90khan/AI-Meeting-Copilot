"""Application DTOs for finalized live-audio chunk processing."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.application.dto.ai.speech import AudioFormat, AudioInput
from app.application.exceptions import ApplicationValidationError
from app.domain.value_objects import MeetingId


class AudioSource(StrEnum):
    """Capture source associated with finalized live audio."""

    MIXED = "mixed"
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"


@dataclass(frozen=True, slots=True, kw_only=True)
class CapturedAudioChunk:
    """One finalized, normalized WAV chunk from an active Meeting capture."""

    meeting_id: MeetingId
    sequence: int
    capture_started_at: datetime
    audio: AudioInput
    source: AudioSource
    overlap_seconds: float = 0.5

    def __post_init__(self) -> None:
        """Validate capture metadata without decoding the WAV payload."""

        if self.sequence < 0:
            raise ApplicationValidationError("Chunk sequence must not be negative.")
        _validate_utc_timestamp(self.capture_started_at, "Capture start timestamp")
        if self.audio.audio_format is not AudioFormat.WAV:
            raise ApplicationValidationError("Live audio chunks must use WAV audio.")
        if self.audio.sample_rate_hz != 16_000:
            raise ApplicationValidationError(
                "Live audio chunks must use a 16000 Hz sample rate."
            )
        if self.audio.channels != 1:
            raise ApplicationValidationError("Live audio chunks must be mono.")
        if not math.isfinite(self.overlap_seconds) or self.overlap_seconds < 0:
            raise ApplicationValidationError(
                "Chunk overlap must be a non-negative finite value."
            )


class LiveTranscriptionStatusKind(StrEnum):
    """Lifecycle and error states emitted by live transcription processing."""

    CAPTURE_ACTIVE = "capture_active"
    PROCESSING = "processing"
    DELAYED = "delayed"
    GAP = "gap"
    PERMISSION_ERROR = "permission_error"
    DEVICE_ERROR = "device_error"
    PROVIDER_ERROR = "provider_error"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveTranscriptionStatus:
    """A user-visible processing status with optional chunk context."""

    kind: LiveTranscriptionStatusKind
    message: str | None = None
    chunk_sequence: int | None = None

    def __post_init__(self) -> None:
        """Validate optional status details."""

        if self.message is not None and not self.message.strip():
            raise ApplicationValidationError("Status message must not be blank.")
        if self.chunk_sequence is not None and self.chunk_sequence < 0:
            raise ApplicationValidationError(
                "Status chunk sequence must not be negative."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessedTranscriptSegment:
    """One accepted transcript segment with a stable UTC timestamp."""

    transcript_id: UUID
    text: str
    timestamp: datetime
    source: AudioSource
    speaker: str = "Unknown"

    def __post_init__(self) -> None:
        """Validate transcript content and timestamp metadata."""

        if not isinstance(self.transcript_id, UUID):
            raise ApplicationValidationError("Transcript ID must be a UUID.")
        if not self.text.strip():
            raise ApplicationValidationError(
                "Transcript segment text must not be blank."
            )
        _validate_utc_timestamp(self.timestamp, "Transcript segment timestamp")
        if not self.speaker.strip():
            raise ApplicationValidationError(
                "Transcript segment speaker must not be blank."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveTranscriptionChunkResult:
    """Accepted results from processing one finalized audio chunk."""

    chunk_sequence: int
    accepted_segments: tuple[ProcessedTranscriptSegment, ...]
    skipped_silence: bool
    status: LiveTranscriptionStatus | None = None

    def __post_init__(self) -> None:
        """Validate the result's chunk identity."""

        if self.chunk_sequence < 0:
            raise ApplicationValidationError(
                "Result chunk sequence must not be negative."
            )


def _validate_utc_timestamp(timestamp: datetime, field_name: str) -> None:
    """Require timezone-aware timestamps with a UTC offset."""

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ApplicationValidationError(f"{field_name} must be timezone-aware.")
    if timestamp.utcoffset() != timedelta(0):
        raise ApplicationValidationError(f"{field_name} must use UTC.")
