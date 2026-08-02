"""Tests for the in-memory PCM16 WAV chunk builder."""

import io
import wave

import pytest
from app.application.dto.ai import AudioFormat
from app.application.exceptions import ApplicationValidationError
from app.infrastructure.audio import WavChunkBuilder


def test_build_creates_a_readable_wav_with_matching_pcm_payload() -> None:
    """Mono PCM16 frames produce a self-describing in-memory WAV payload."""

    pcm_frames = b"\x00\x00\xff\x7f\x00\x80"

    audio_input = WavChunkBuilder().build(pcm_frames)

    assert audio_input.audio_format is AudioFormat.WAV
    assert audio_input.sample_rate_hz == 16_000
    assert audio_input.channels == 1
    with wave.open(io.BytesIO(audio_input.data), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == 3
        assert wav_file.readframes(wav_file.getnframes()) == pcm_frames


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sample_rate_hz": 0}, "Sample rate"),
        ({"channels": 0}, "Channels"),
        ({"sample_width_bytes": 1}, "16-bit"),
    ],
)
def test_constructor_rejects_unsupported_pcm_format(
    kwargs: dict[str, int],
    message: str,
) -> None:
    """V1 accepts only valid sample rates, channels, and PCM16 widths."""

    with pytest.raises(ApplicationValidationError, match=message):
        WavChunkBuilder(**kwargs)


def test_build_rejects_empty_pcm_frames() -> None:
    """WAV construction requires at least one PCM frame."""

    with pytest.raises(ApplicationValidationError, match="must not be empty"):
        WavChunkBuilder().build(b"")


def test_build_rejects_pcm_data_that_does_not_align_to_frames() -> None:
    """PCM byte length must contain complete interleaved frames."""

    with pytest.raises(ApplicationValidationError, match="complete frames"):
        WavChunkBuilder(channels=2).build(b"\x00\x00")
