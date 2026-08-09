"""Structural tests for the V1 independently playable recording WAV contract."""

import struct

import pytest
from app.application.exceptions import RecordingSegmentCorruptError
from app.infrastructure.audio import WavChunkBuilder
from app.infrastructure.recordings.wav_recording_segment import (
    validate_wav_recording_segment,
)


def _wav(*, sample_rate_hz: int = 16_000, channels: int = 1) -> bytes:
    return (
        WavChunkBuilder(
            sample_rate_hz=sample_rate_hz,
            channels=channels,
        )
        .build(b"\x00\x00" * channels)
        .data
    )


def _reject(data: bytes) -> None:
    with pytest.raises(RecordingSegmentCorruptError) as error:
        validate_wav_recording_segment(data)
    assert "RIFF" not in str(error.value)
    assert "WAVE" not in str(error.value)
    assert "path" not in str(error.value)


def test_valid_pcm16_mono_16khz_wav_segment_is_accepted() -> None:
    validate_wav_recording_segment(_wav())


@pytest.mark.parametrize(("offset", "value"), [(0, b"NOPE"), (8, b"NOPE")])
def test_invalid_riff_or_wave_markers_are_rejected(offset: int, value: bytes) -> None:
    data = bytearray(_wav())
    data[offset : offset + 4] = value

    _reject(bytes(data))


def test_stereo_and_wrong_sample_rate_are_rejected() -> None:
    _reject(_wav(channels=2))
    _reject(_wav(sample_rate_hz=8_000))


def test_non_pcm_bit_depth_and_inconsistent_data_length_are_rejected() -> None:
    non_pcm = bytearray(_wav())
    non_pcm[20:22] = struct.pack("<H", 3)
    _reject(bytes(non_pcm))

    wrong_bit_depth = bytearray(_wav())
    wrong_bit_depth[34:36] = struct.pack("<H", 8)
    _reject(bytes(wrong_bit_depth))

    malformed_length = bytearray(_wav())
    malformed_length[40:44] = struct.pack("<I", 3)
    _reject(bytes(malformed_length))
