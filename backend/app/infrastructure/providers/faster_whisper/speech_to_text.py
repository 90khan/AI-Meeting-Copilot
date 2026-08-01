"""Faster-Whisper adapter for the speech-to-text application contract."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from io import BytesIO

from app.application.dto.ai import (
    AudioFormat,
    LanguageCode,
    SpeechToTextRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.infrastructure.providers.faster_whisper.model_manager import (
    FasterWhisperModelManager,
)


class FasterWhisperSpeechToTextProvider:
    """Transcribe complete WAV inputs with a local Faster-Whisper model."""

    def __init__(
        self,
        *,
        model_manager: FasterWhisperModelManager,
        beam_size: int,
        vad_enabled: bool,
    ) -> None:
        """Store the model lifecycle manager and transcription options."""

        self._model_manager = model_manager
        self._beam_size = beam_size
        self._vad_enabled = vad_enabled

    async def transcribe(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Transcribe WAV audio without blocking the event loop."""

        if request.audio.audio_format is not AudioFormat.WAV:
            raise InvalidProviderResponseError(
                "Unsupported audio format for Faster-Whisper."
            )

        try:
            return await asyncio.to_thread(self._transcribe_blocking, request)
        except asyncio.CancelledError:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except ProviderUnavailableError:
            raise
        except InvalidProviderResponseError:
            raise
        except Exception as error:
            raise InvalidProviderResponseError(
                "Faster-Whisper transcription failed."
            ) from error

    def _transcribe_blocking(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Run model inference and consume all returned segments synchronously."""

        model = self._model_manager.get_model()
        transcription_kwargs: dict[str, object] = {
            "beam_size": self._beam_size,
            "vad_filter": self._vad_enabled,
        }
        if request.language_hint is not None:
            transcription_kwargs["language"] = request.language_hint.value.split(
                "-", maxsplit=1
            )[0]

        audio_stream = BytesIO(request.audio.data)
        provider_segments, transcription_info = model.transcribe(
            audio_stream, **transcription_kwargs
        )
        segments = self._map_segments(provider_segments)
        language = self._map_language(transcription_info)
        duration_seconds = self._map_duration(transcription_info, segments)

        return TranscriptionResult(
            language=language,
            segments=segments,
            duration_seconds=duration_seconds,
        )

    @staticmethod
    def _map_segments(
        provider_segments: Iterable[object],
    ) -> tuple[TranscriptionSegment, ...]:
        """Convert valid provider segments while consuming their iterator."""

        segments: list[TranscriptionSegment] = []
        for provider_segment in provider_segments:
            text = getattr(provider_segment, "text", None)
            if not isinstance(text, str):
                raise InvalidProviderResponseError(
                    "Faster-Whisper returned a segment with invalid text."
                )

            normalized_text = text.strip()
            if not normalized_text:
                continue

            start_seconds = _read_timestamp(provider_segment, "start")
            end_seconds = _read_timestamp(provider_segment, "end")
            if start_seconds < 0 or end_seconds <= start_seconds:
                raise InvalidProviderResponseError(
                    "Faster-Whisper returned a segment with invalid timing."
                )

            segments.append(
                TranscriptionSegment(
                    text=normalized_text,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    speaker=None,
                )
            )

        return tuple(segments)

    @staticmethod
    def _map_language(transcription_info: object) -> LanguageCode:
        """Validate and convert the provider-reported detected language."""

        provider_language = getattr(transcription_info, "language", None)
        if not isinstance(provider_language, str) or not provider_language:
            raise InvalidProviderResponseError(
                "Faster-Whisper returned no detected language."
            )

        try:
            return LanguageCode(value=provider_language)
        except ValueError as error:
            raise InvalidProviderResponseError(
                "Faster-Whisper returned an invalid detected language."
            ) from error

    @staticmethod
    def _map_duration(
        transcription_info: object, segments: tuple[TranscriptionSegment, ...]
    ) -> float:
        """Use valid provider duration or the final mapped segment end."""

        final_segment_end = segments[-1].end_seconds if segments else 0.0
        provider_duration = getattr(transcription_info, "duration", None)
        if provider_duration is None:
            return final_segment_end
        if isinstance(provider_duration, bool) or not isinstance(
            provider_duration, (int, float)
        ):
            raise InvalidProviderResponseError(
                "Faster-Whisper returned an invalid transcription duration."
            )

        duration_seconds = float(provider_duration)
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise InvalidProviderResponseError(
                "Faster-Whisper returned an invalid transcription duration."
            )
        return max(duration_seconds, final_segment_end)


def _read_timestamp(provider_segment: object, attribute_name: str) -> float:
    """Return one finite numeric segment timestamp from the provider response."""

    timestamp = getattr(provider_segment, attribute_name, None)
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise InvalidProviderResponseError(
            "Faster-Whisper returned a segment with invalid timing."
        )

    normalized_timestamp = float(timestamp)
    if not math.isfinite(normalized_timestamp):
        raise InvalidProviderResponseError(
            "Faster-Whisper returned a segment with invalid timing."
        )
    return normalized_timestamp
