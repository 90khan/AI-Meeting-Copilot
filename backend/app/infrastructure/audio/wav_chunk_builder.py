"""In-memory WAV construction for normalized PCM16 audio frames."""

import io
import wave

from app.application.dto.ai import AudioFormat, AudioInput
from app.application.exceptions import ApplicationValidationError


class WavChunkBuilder:
    """Build self-describing WAV payloads from interleaved PCM16 frames."""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 16_000,
        channels: int = 1,
        sample_width_bytes: int = 2,
    ) -> None:
        """Initialize the fixed PCM format used for built WAV chunks."""

        if sample_rate_hz <= 0:
            raise ApplicationValidationError("Sample rate must be greater than zero.")
        if channels <= 0:
            raise ApplicationValidationError("Channels must be greater than zero.")
        if sample_width_bytes != 2:
            raise ApplicationValidationError(
                "V1 WAV chunks require 16-bit PCM samples."
            )

        self._sample_rate_hz = sample_rate_hz
        self._channels = channels
        self._sample_width_bytes = sample_width_bytes

    def build(self, pcm_frames: bytes) -> AudioInput:
        """Build an in-memory WAV AudioInput from aligned PCM16 frame bytes."""

        if not pcm_frames:
            raise ApplicationValidationError("PCM frames must not be empty.")
        if len(pcm_frames) % self._frame_width_bytes != 0:
            raise ApplicationValidationError(
                "PCM frames must align with complete frames."
            )

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(self._sample_width_bytes)
            wav_file.setframerate(self._sample_rate_hz)
            wav_file.writeframes(pcm_frames)

        return AudioInput(
            data=wav_buffer.getvalue(),
            sample_rate_hz=self._sample_rate_hz,
            channels=self._channels,
            audio_format=AudioFormat.WAV,
        )

    @property
    def _frame_width_bytes(self) -> int:
        """Return the byte width of one interleaved PCM frame."""

        return self._channels * self._sample_width_bytes
