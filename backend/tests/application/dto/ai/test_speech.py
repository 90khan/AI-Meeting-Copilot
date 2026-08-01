"""Tests for speech-to-text application DTOs."""

from dataclasses import FrozenInstanceError

import pytest
from app.application.dto.ai import (
    AudioInput,
    LanguageCode,
    SpeechToTextRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.application.exceptions import ApplicationValidationError


def make_audio() -> AudioInput:
    """Create valid audio input for tests."""

    return AudioInput(data=b"audio", sample_rate_hz=16_000, channels=1)


def make_segment(
    *, start_seconds: float = 0.0, end_seconds: float = 1.0
) -> TranscriptionSegment:
    """Create a valid transcription segment for tests."""

    return TranscriptionSegment(
        text="Hello",
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )


def test_audio_input_is_immutable() -> None:
    """Audio input cannot be changed after construction."""

    audio = make_audio()

    with pytest.raises(FrozenInstanceError):
        audio.channels = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("data", "sample_rate_hz", "channels"),
    [(b"", 16_000, 1), (b"audio", 0, 1), (b"audio", 16_000, 0)],
)
def test_audio_input_rejects_malformed_values(
    data: bytes, sample_rate_hz: int, channels: int
) -> None:
    """Audio input rejects empty data and non-positive capture properties."""

    with pytest.raises(ApplicationValidationError):
        AudioInput(data=data, sample_rate_hz=sample_rate_hz, channels=channels)


@pytest.mark.parametrize(
    ("text", "start_seconds", "end_seconds", "speaker"),
    [
        ("", 0.0, 1.0, None),
        ("Hello", -0.1, 1.0, None),
        ("Hello", 1.0, 1.0, None),
        ("Hello", 2.0, 1.0, None),
        ("Hello", 0.0, 1.0, " "),
    ],
)
def test_transcription_segment_rejects_malformed_values(
    text: str, start_seconds: float, end_seconds: float, speaker: str | None
) -> None:
    """Segments require valid text, timing, and optional speaker values."""

    with pytest.raises(ApplicationValidationError):
        TranscriptionSegment(
            text=text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            speaker=speaker,
        )


def test_transcription_result_rejects_segment_beyond_duration() -> None:
    """A segment may not end after the total transcription duration."""

    with pytest.raises(ApplicationValidationError):
        TranscriptionResult(
            language=LanguageCode(value="en"),
            segments=(make_segment(end_seconds=2.0),),
            duration_seconds=1.0,
        )


def test_transcription_result_rejects_out_of_order_segments() -> None:
    """Segments must be ordered by non-decreasing start time."""

    with pytest.raises(ApplicationValidationError):
        TranscriptionResult(
            language=LanguageCode(value="en"),
            segments=(
                make_segment(start_seconds=1.0, end_seconds=2.0),
                make_segment(start_seconds=0.0, end_seconds=0.5),
            ),
            duration_seconds=2.0,
        )


@pytest.mark.parametrize("duration_seconds", [-0.1, float("inf")])
def test_transcription_result_rejects_invalid_duration(duration_seconds: float) -> None:
    """The total duration must be non-negative and finite."""

    with pytest.raises(ApplicationValidationError):
        TranscriptionResult(
            language=LanguageCode(value="en"),
            segments=(),
            duration_seconds=duration_seconds,
        )


def test_speech_to_text_request_allows_an_optional_language_hint() -> None:
    """A request may be created with or without a language hint."""

    audio = make_audio()

    assert SpeechToTextRequest(audio=audio).language_hint is None
    assert SpeechToTextRequest(
        audio=audio, language_hint=LanguageCode(value="de")
    ).language_hint == LanguageCode(value="de")
