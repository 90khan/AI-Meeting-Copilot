"""Tests for the Faster-Whisper speech-to-text adapter."""

import asyncio
from dataclasses import dataclass
from typing import cast

import pytest
from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    LanguageCode,
    SpeechToTextRequest,
)
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.infrastructure.providers.faster_whisper import (
    FasterWhisperModelManager,
    FasterWhisperSpeechToTextProvider,
)
from app.infrastructure.providers.faster_whisper import (
    speech_to_text as speech_to_text_module,
)


@dataclass
class FakeSegment:
    """Minimal provider segment used by adapter tests."""

    text: str
    start: float
    end: float


@dataclass
class FakeInfo:
    """Minimal provider transcription metadata used by adapter tests."""

    language: object
    duration: object | None = None


class FakeModel:
    """Minimal Faster-Whisper model double."""

    def __init__(self, segments: object, info: object) -> None:
        """Store model output and record invocation data."""

        self._segments = segments
        self._info = info
        self.calls: list[tuple[bytes, dict[str, object]]] = []

    def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
        """Return configured fake output after recording the input stream."""

        self.calls.append((audio.getvalue(), kwargs))  # type: ignore[union-attr]
        return self._segments, self._info


class FakeModelManager:
    """Minimal model manager double."""

    def __init__(self, model: object | BaseException) -> None:
        """Store the configured model or failure."""

        self._model = model

    def get_model(self) -> object:
        """Return the model or raise the configured failure."""

        if isinstance(self._model, BaseException):
            raise self._model
        return self._model


def make_request(language_hint: LanguageCode | None = None) -> SpeechToTextRequest:
    """Create a valid WAV transcription request."""

    return SpeechToTextRequest(
        audio=AudioInput(
            data=b"wav-data",
            sample_rate_hz=16_000,
            channels=1,
            audio_format=AudioFormat.WAV,
        ),
        language_hint=language_hint,
    )


def make_provider(model: object | BaseException) -> FasterWhisperSpeechToTextProvider:
    """Create the adapter with a fake model manager."""

    return FasterWhisperSpeechToTextProvider(
        model_manager=cast(FasterWhisperModelManager, FakeModelManager(model)),
        beam_size=3,
        vad_enabled=True,
    )


def test_wav_bytes_and_transcription_options_are_forwarded() -> None:
    """The adapter forwards WAV bytes through BytesIO with configured options."""

    model = FakeModel([], FakeInfo(language="en", duration=0.0))
    result = asyncio.run(make_provider(model).transcribe(make_request()))

    assert result.segments == ()
    assert model.calls == [
        (
            b"wav-data",
            {"beam_size": 3, "vad_filter": True},
        )
    ]


def test_language_hint_is_reduced_to_its_primary_subtag() -> None:
    """Regional language hints are reduced for Faster-Whisper."""

    model = FakeModel([], FakeInfo(language="de", duration=0.0))

    asyncio.run(
        make_provider(model).transcribe(make_request(LanguageCode(value="de-DE")))
    )

    assert model.calls[0][1]["language"] == "de"


def test_absent_language_hint_allows_automatic_detection() -> None:
    """No language argument is sent when automatic detection is requested."""

    model = FakeModel([], FakeInfo(language="tr", duration=0.0))

    asyncio.run(make_provider(model).transcribe(make_request()))

    assert "language" not in model.calls[0][1]


def test_segments_are_fully_consumed_mapped_and_blank_segments_skipped() -> None:
    """All provider output is consumed and valid non-blank segments are mapped."""

    consumed: list[int] = []

    def segment_iterator() -> object:
        for index, segment in enumerate(
            [
                FakeSegment(text="  Hello  ", start=0.0, end=1.5),
                FakeSegment(text="  ", start=1.5, end=2.0),
                FakeSegment(text="World", start=2.0, end=3.0),
            ]
        ):
            consumed.append(index)
            yield segment

    model = FakeModel(segment_iterator(), FakeInfo(language="en", duration=3.0))

    result = asyncio.run(make_provider(model).transcribe(make_request()))

    assert consumed == [0, 1, 2]
    assert [
        (segment.text, segment.start_seconds, segment.end_seconds)
        for segment in result.segments
    ] == [("Hello", 0.0, 1.5), ("World", 2.0, 3.0)]
    assert all(segment.speaker is None for segment in result.segments)


def test_detected_language_and_duration_fallback_are_mapped() -> None:
    """Detected language is preserved and short duration falls back to segment end."""

    model = FakeModel(
        [FakeSegment(text="Merhaba", start=0.0, end=2.5)],
        FakeInfo(language="tr", duration=1.0),
    )

    result = asyncio.run(make_provider(model).transcribe(make_request()))

    assert result.language == LanguageCode(value="tr")
    assert result.duration_seconds == 2.5


def test_unsupported_audio_format_is_rejected() -> None:
    """Only WAV input is accepted by the V1 adapter."""

    request = make_request()
    object.__setattr__(request.audio, "audio_format", cast(AudioFormat, "mp3"))

    with pytest.raises(InvalidProviderResponseError, match="Unsupported audio format"):
        asyncio.run(make_provider(FakeModel([], FakeInfo("en"))).transcribe(request))


@pytest.mark.parametrize(
    "segment",
    [
        FakeSegment(text="Hello", start=-1.0, end=1.0),
        FakeSegment(text="Hello", start=1.0, end=1.0),
        FakeSegment(text="Hello", start=float("nan"), end=1.0),
    ],
)
def test_malformed_segment_is_rejected(segment: FakeSegment) -> None:
    """Provider segments must carry valid finite, increasing timing."""

    model = FakeModel([segment], FakeInfo(language="en", duration=1.0))

    with pytest.raises(InvalidProviderResponseError, match="invalid timing"):
        asyncio.run(make_provider(model).transcribe(make_request()))


@pytest.mark.parametrize("language", [None, "", "not a language"])
def test_missing_or_malformed_provider_language_is_rejected(language: object) -> None:
    """The adapter requires a valid provider-reported detected language."""

    model = FakeModel([], FakeInfo(language=language, duration=0.0))

    with pytest.raises(InvalidProviderResponseError):
        asyncio.run(make_provider(model).transcribe(make_request()))


def test_model_and_transcription_failures_become_invalid_provider_responses() -> None:
    """Unexpected model failures stay behind the provider error boundary."""

    class FailingModel:
        def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
            raise RuntimeError("decode failure")

    with pytest.raises(InvalidProviderResponseError, match="transcription failed"):
        asyncio.run(make_provider(FailingModel()).transcribe(make_request()))


def test_provider_unavailable_error_is_preserved() -> None:
    """Model manager availability failures are not remapped."""

    with pytest.raises(ProviderUnavailableError, match="unavailable"):
        asyncio.run(
            make_provider(ProviderUnavailableError("model unavailable")).transcribe(
                make_request()
            )
        )


def test_cancellation_propagates_unchanged() -> None:
    """Cancellation is never converted into a provider response error."""

    class CancellingModel:
        def transcribe(self, audio: object, **kwargs: object) -> tuple[object, object]:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(make_provider(CancellingModel()).transcribe(make_request()))


def test_inference_is_dispatched_through_asyncio_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter delegates blocking inference through asyncio.to_thread."""

    calls: list[object] = []

    async def fake_to_thread(function: object, *args: object) -> object:
        calls.append(function)
        return function(*args)  # type: ignore[operator]

    monkeypatch.setattr(speech_to_text_module.asyncio, "to_thread", fake_to_thread)
    model = FakeModel([], FakeInfo(language="en", duration=0.0))

    asyncio.run(make_provider(model).transcribe(make_request()))

    assert len(calls) == 1
