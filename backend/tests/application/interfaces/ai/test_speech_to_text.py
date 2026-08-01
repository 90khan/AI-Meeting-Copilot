"""Tests for the speech-to-text provider contract."""

import inspect

from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    LanguageCode,
    SpeechToTextRequest,
    TranscriptionResult,
)
from app.application.interfaces.ai import SpeechToTextProvider


class FakeSpeechToTextProvider:
    """Minimal structural implementation of the provider contract."""

    async def transcribe(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Return an empty result for the supplied request."""

        return TranscriptionResult(
            language=request.language_hint or LanguageCode(value="en"),
            segments=(),
            duration_seconds=0.0,
        )


def test_speech_to_text_provider_method_is_asynchronous() -> None:
    """The provider contract exposes an asynchronous transcription method."""

    assert inspect.iscoroutinefunction(SpeechToTextProvider.transcribe)


def test_fake_provider_structurally_satisfies_the_protocol() -> None:
    """A capability implementation needs no nominal inheritance."""

    provider = FakeSpeechToTextProvider()

    assert isinstance(provider, SpeechToTextProvider)
    assert provider.transcribe


def test_fake_provider_can_be_assigned_to_the_protocol() -> None:
    """Static protocol typing accepts a structurally compatible provider."""

    provider: SpeechToTextProvider = FakeSpeechToTextProvider()

    assert isinstance(provider, SpeechToTextProvider)


def test_fake_provider_accepts_a_valid_request() -> None:
    """The fake uses the public request and result DTOs."""

    request = SpeechToTextRequest(
        audio=AudioInput(
            data=b"audio",
            sample_rate_hz=16_000,
            channels=1,
            audio_format=AudioFormat.WAV,
        ),
        language_hint=LanguageCode(value="tr"),
    )

    assert request.language_hint == LanguageCode(value="tr")
