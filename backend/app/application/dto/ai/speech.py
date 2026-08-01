"""Speech-to-text application data transfer objects."""

import math
from dataclasses import dataclass

from app.application.dto.ai.language import LanguageCode
from app.application.exceptions import ApplicationValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioInput:
    """Audio data and its basic capture characteristics."""

    data: bytes
    sample_rate_hz: int
    channels: int

    def __post_init__(self) -> None:
        """Validate the audio payload and capture characteristics."""

        if not self.data:
            raise ApplicationValidationError("Audio data must not be empty.")
        if self.sample_rate_hz <= 0:
            raise ApplicationValidationError(
                "Audio sample rate must be greater than zero."
            )
        if self.channels <= 0:
            raise ApplicationValidationError(
                "Audio channels must be greater than zero."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptionSegment:
    """A timed section of recognized speech."""

    text: str
    start_seconds: float
    end_seconds: float
    speaker: str | None = None

    def __post_init__(self) -> None:
        """Validate the recognized text and timing bounds."""

        if not self.text.strip():
            raise ApplicationValidationError(
                "Transcription segment text must not be blank."
            )
        if not math.isfinite(self.start_seconds) or self.start_seconds < 0:
            raise ApplicationValidationError(
                "Transcription segment start must be a non-negative finite value."
            )
        if (
            not math.isfinite(self.end_seconds)
            or self.end_seconds <= self.start_seconds
        ):
            raise ApplicationValidationError(
                "Transcription segment end must be greater than its start."
            )
        if self.speaker is not None and not self.speaker.strip():
            raise ApplicationValidationError(
                "Transcription segment speaker must not be blank."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptionResult:
    """The complete result returned by a speech-to-text provider."""

    language: LanguageCode
    segments: tuple[TranscriptionSegment, ...]
    duration_seconds: float

    def __post_init__(self) -> None:
        """Validate the overall duration and segment chronology."""

        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ApplicationValidationError(
                "Transcription duration must be a non-negative finite value."
            )

        previous_start_seconds: float | None = None
        for segment in self.segments:
            if segment.end_seconds > self.duration_seconds:
                raise ApplicationValidationError(
                    "Transcription segment end must not exceed the total duration."
                )
            if (
                previous_start_seconds is not None
                and segment.start_seconds < previous_start_seconds
            ):
                raise ApplicationValidationError(
                    "Transcription segments must be ordered by start time."
                )
            previous_start_seconds = segment.start_seconds


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechToTextRequest:
    """Input supplied to a speech-to-text capability."""

    audio: AudioInput
    language_hint: LanguageCode | None = None
