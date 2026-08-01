"""Opt-in smoke test for local Faster-Whisper WAV transcription."""

import asyncio
import os
import wave
from pathlib import Path

import pytest
from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    SpeechToTextRequest,
    TranscriptionResult,
)
from app.core.config import Settings
from app.core.container import Container


@pytest.mark.local_ai
def test_local_faster_whisper_transcribes_a_developer_supplied_wav() -> None:
    """Run real local transcription only with explicit developer opt-in."""

    if os.environ.get("AI_MEETING_COPILOT_RUN_LOCAL_AI_TESTS") != "1":
        pytest.skip(
            "Set AI_MEETING_COPILOT_RUN_LOCAL_AI_TESTS=1 to enable local AI tests."
        )

    wav_path = _require_wav_path()
    wav_bytes, sample_rate_hz, channels = _read_wav(wav_path)
    settings = Settings()
    if settings.speech_to_text_provider != "faster-whisper":
        pytest.skip(
            "Set AI_MEETING_COPILOT_SPEECH_TO_TEXT_PROVIDER=faster-whisper "
            "to run this smoke test."
        )

    request = SpeechToTextRequest(
        audio=AudioInput(
            data=wav_bytes,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            audio_format=AudioFormat.WAV,
        )
    )
    result = asyncio.run(_transcribe_with_container(settings, request))

    assert str(result.language).strip()
    assert result.duration_seconds > 0
    assert result.segments
    assert all(segment.text.strip() for segment in result.segments)
    assert all(
        segment.start_seconds >= 0
        and segment.end_seconds > segment.start_seconds
        and segment.end_seconds <= result.duration_seconds
        for segment in result.segments
    )


def _require_wav_path() -> Path:
    """Return the configured readable WAV file or skip the local smoke test."""

    wav_path_value = os.environ.get("AI_MEETING_COPILOT_TEST_WAV_PATH")
    if not wav_path_value:
        pytest.skip(
            "Set AI_MEETING_COPILOT_TEST_WAV_PATH to a readable local WAV file."
        )

    wav_path = Path(wav_path_value).expanduser()
    if not wav_path.is_file():
        pytest.skip(
            "AI_MEETING_COPILOT_TEST_WAV_PATH must reference a readable WAV file."
        )

    return wav_path


def _read_wav(wav_path: Path) -> tuple[bytes, int, int]:
    """Validate the WAV header and return bytes with audio metadata."""

    try:
        with wave.open(str(wav_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_rate_hz = wav_file.getframerate()
            frame_count = wav_file.getnframes()
    except (OSError, wave.Error):
        pytest.skip("AI_MEETING_COPILOT_TEST_WAV_PATH must reference a valid WAV file.")

    if channels <= 0 or sample_rate_hz <= 0 or frame_count <= 0:
        pytest.skip("AI_MEETING_COPILOT_TEST_WAV_PATH has an invalid WAV header.")

    try:
        return wav_path.read_bytes(), sample_rate_hz, channels
    except OSError:
        pytest.skip(
            "AI_MEETING_COPILOT_TEST_WAV_PATH must reference a readable WAV file."
        )


async def _transcribe_with_container(
    settings: Settings, request: SpeechToTextRequest
) -> TranscriptionResult:
    """Run one transcription while always stopping the public container."""

    container = Container(settings)
    await container.start()
    try:
        provider = container.get_speech_to_text_provider()
        return await provider.transcribe(request)
    finally:
        await container.stop()
