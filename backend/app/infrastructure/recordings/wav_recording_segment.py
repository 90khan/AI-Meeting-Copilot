"""Validation for the V1 self-contained WAV recording-segment contract."""

import struct

from app.application.exceptions import RecordingSegmentCorruptError

_RIFF_HEADER = struct.Struct("<4sI4s")
_CHUNK_HEADER = struct.Struct("<4sI")
_PCM_FORMAT = struct.Struct("<HHIIHH")

_SAMPLE_RATE_HZ = 16_000
_CHANNELS = 1
_SAMPLE_WIDTH_BITS = 16
_BLOCK_ALIGNMENT = 2
_BYTE_RATE = 32_000


def validate_wav_recording_segment(data: bytes) -> int:
    """Validate one complete V1 WAV segment and return its PCM frame count.

    Segments are independent, five-second-target WAV files (with a permitted
    shorter final segment), never overlapping transcription chunks.
    """

    if not isinstance(data, bytes) or len(data) < _RIFF_HEADER.size:
        raise RecordingSegmentCorruptError()
    riff, declared_size, wave = _RIFF_HEADER.unpack_from(data)
    if riff != b"RIFF" or wave != b"WAVE" or declared_size != len(data) - 8:
        raise RecordingSegmentCorruptError()

    position = _RIFF_HEADER.size
    format_chunk: bytes | None = None
    data_chunk: bytes | None = None
    while position < len(data):
        if position + _CHUNK_HEADER.size > len(data):
            raise RecordingSegmentCorruptError()
        chunk_type, chunk_length = _CHUNK_HEADER.unpack_from(data, position)
        position += _CHUNK_HEADER.size
        end = position + chunk_length
        if end > len(data) or chunk_type not in {b"fmt ", b"data"}:
            raise RecordingSegmentCorruptError()
        chunk = data[position:end]
        if chunk_type == b"fmt " and format_chunk is None:
            format_chunk = chunk
        elif chunk_type == b"data" and data_chunk is None:
            data_chunk = chunk
        else:
            raise RecordingSegmentCorruptError()
        position = end + (chunk_length % 2)

    if position != len(data) or format_chunk is None or data_chunk is None:
        raise RecordingSegmentCorruptError()
    if len(format_chunk) != _PCM_FORMAT.size:
        raise RecordingSegmentCorruptError()
    (
        format_code,
        channels,
        sample_rate_hz,
        byte_rate,
        block_alignment,
        bits_per_sample,
    ) = _PCM_FORMAT.unpack(format_chunk)
    block_alignment = int(block_alignment)
    if (
        format_code != 1
        or channels != _CHANNELS
        or sample_rate_hz != _SAMPLE_RATE_HZ
        or bits_per_sample != _SAMPLE_WIDTH_BITS
        or block_alignment != _BLOCK_ALIGNMENT
        or byte_rate != _BYTE_RATE
        or len(data_chunk) == 0
        or len(data_chunk) % block_alignment != 0
    ):
        raise RecordingSegmentCorruptError()

    return len(data_chunk) // block_alignment
