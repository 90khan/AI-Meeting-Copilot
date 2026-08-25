"""Faster-Whisper adapter for the speech-to-text application contract."""

from __future__ import annotations

import asyncio
import math
import time
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
from app.application.services.live_transcription_priority_gate import (
    LiveTranscriptionPriorityGate,
)
from app.core.logging import get_logger
from app.core.throughput_diagnostics import (
    active_ollama_requests,
    current_sequence_fields,
    emit_throughput,
    estimate_v1_pcm16_wav_duration_ms,
)
from app.infrastructure.providers.faster_whisper.model_manager import (
    FasterWhisperModelManager,
)

_LOGGER = get_logger(__name__)


class FasterWhisperSpeechToTextProvider:
    """Transcribe complete WAV inputs with a local Faster-Whisper model."""

    def __init__(
        self,
        *,
        model_manager: FasterWhisperModelManager,
        beam_size: int,
        vad_enabled: bool,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        cpu_threads: int | None = None,
        priority_gate: LiveTranscriptionPriorityGate | None = None,
    ) -> None:
        """Store the model lifecycle manager and transcription options."""

        self._model_manager = model_manager
        self._beam_size = beam_size
        self._vad_enabled = vad_enabled
        self._active_request_count = 0
        self._request_ordinal = 0
        self._priority_gate = priority_gate or LiveTranscriptionPriorityGate()
        self._emit_configuration(
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
        )

    async def transcribe(self, request: SpeechToTextRequest) -> TranscriptionResult:
        """Transcribe WAV audio without blocking the event loop."""

        if request.audio.audio_format is not AudioFormat.WAV:
            raise InvalidProviderResponseError(
                "Unsupported audio format for Faster-Whisper."
            )

        submitted_at = time.monotonic()
        self._request_ordinal += 1
        request_ordinal = self._request_ordinal
        first_request = request_ordinal == 1
        self._active_request_count += 1
        active_request_count = self._active_request_count
        outcome = "completed"
        self._priority_gate.stt_started()
        emit_throughput(
            "stt_provider_started",
            **current_sequence_fields(),
            ordinal=request_ordinal,
            active_requests=active_request_count,
            ollama_active_requests=active_ollama_requests(),
            first_request="yes" if first_request else "no",
        )
        _LOGGER.debug(
            "live-transcription faster-whisper transcription started "
            "active_requests=%d",
            active_request_count,
        )
        try:
            result = await asyncio.to_thread(
                self._transcribe_blocking_with_diagnostics,
                request,
                submitted_at,
                active_request_count,
                request_ordinal,
                first_request,
            )
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except (KeyboardInterrupt, SystemExit):
            outcome = "failed"
            raise
        except ProviderUnavailableError:
            outcome = "failed"
            _LOGGER.debug(
                "live-transcription faster-whisper transcription failed "
                "stage=provider_unavailable"
            )
            raise
        except InvalidProviderResponseError:
            outcome = "failed"
            _LOGGER.debug(
                "live-transcription faster-whisper transcription failed "
                "stage=provider_response"
            )
            raise
        except Exception as error:
            outcome = "failed"
            _LOGGER.debug(
                "live-transcription faster-whisper transcription failed "
                "stage=provider_execution"
            )
            raise InvalidProviderResponseError(
                "Faster-Whisper transcription failed."
            ) from error
        finally:
            self._priority_gate.stt_finished()
            self._active_request_count -= 1
            elapsed_ms = _elapsed_milliseconds(submitted_at)
            _LOGGER.debug(
                "live-transcription faster-whisper transcription completed "
                "outcome=%s elapsed_ms=%d active_requests=%d",
                outcome,
                elapsed_ms,
                self._active_request_count,
            )
            emit_throughput(
                "stt_worker_completed",
                **current_sequence_fields(),
                outcome=outcome,
                elapsed_ms=elapsed_ms,
                active_requests=self._active_request_count,
                ollama_active_requests=active_ollama_requests(),
                ordinal=request_ordinal,
                first_request="yes" if first_request else "no",
            )
        _LOGGER.debug(
            "live-transcription faster-whisper transcription segments mapped "
            "segment_count=%d",
            len(result.segments),
        )
        return result

    def _transcribe_blocking_with_diagnostics(
        self,
        request: SpeechToTextRequest,
        submitted_at: float,
        active_request_count: int,
        request_ordinal: int,
        first_request: bool,
    ) -> TranscriptionResult:
        """Run the blocking provider while measuring executor scheduling delay."""

        emit_throughput(
            "stt_worker_started",
            **current_sequence_fields(),
            queue_wait_ms=_elapsed_milliseconds(submitted_at),
            active_requests=active_request_count,
            ordinal=request_ordinal,
            first_request="yes" if first_request else "no",
        )
        return self._transcribe_blocking(
            request,
            request_ordinal=request_ordinal,
            first_request=first_request,
        )

    def _transcribe_blocking(
        self,
        request: SpeechToTextRequest,
        *,
        request_ordinal: int,
        first_request: bool,
    ) -> TranscriptionResult:
        """Run model inference and consume all returned segments synchronously."""

        model_started_at = time.monotonic()
        model, loaded_on_this_call = self._model_manager.get_model_with_load_state()
        emit_throughput(
            "stt_model_ready",
            **current_sequence_fields(),
            ordinal=request_ordinal,
            model_load_ms=_elapsed_milliseconds(model_started_at),
            model_loaded="yes" if loaded_on_this_call else "no",
            first_request="yes" if first_request else "no",
        )
        transcription_kwargs: dict[str, object] = {
            "beam_size": self._beam_size,
            "vad_filter": self._vad_enabled,
        }
        if request.language_hint is not None:
            transcription_kwargs["language"] = request.language_hint.value.split(
                "-", maxsplit=1
            )[0]

        audio_duration_ms = estimate_v1_pcm16_wav_duration_ms(
            byte_length=len(request.audio.data),
            sample_rate_hz=request.audio.sample_rate_hz,
            channels=request.audio.channels,
        )
        audio_stream = BytesIO(request.audio.data)
        audio_decode_conversion_started_at = time.monotonic()
        provider_segments, transcription_info = model.transcribe(
            audio_stream, **transcription_kwargs
        )
        emit_throughput(
            "stt_audio_decode_conversion_completed",
            **current_sequence_fields(),
            ordinal=request_ordinal,
            audio_decode_conversion_ms=_elapsed_milliseconds(
                audio_decode_conversion_started_at
            ),
            preprocessing_ms=_elapsed_milliseconds(audio_decode_conversion_started_at),
            first_request="yes" if first_request else "no",
        )
        inference_started_at = time.monotonic()
        segments = self._map_segments(provider_segments)
        language = self._map_language(transcription_info)
        duration_seconds = self._map_duration(transcription_info, segments)
        emit_throughput(
            "stt_inference_completed",
            **current_sequence_fields(),
            ordinal=request_ordinal,
            stt_inference_ms=_elapsed_milliseconds(inference_started_at),
            first_request="yes" if first_request else "no",
        )
        stt_duration_ms = _elapsed_milliseconds(audio_decode_conversion_started_at)
        emit_throughput(
            "stt_transcription_completed",
            **current_sequence_fields(),
            ordinal=request_ordinal,
            stt_duration_ms=stt_duration_ms,
            stt_rtf_milli=(
                (stt_duration_ms * 1_000 // audio_duration_ms)
                if audio_duration_ms > 0
                else 0
            ),
            first_request="yes" if first_request else "no",
        )

        return TranscriptionResult(
            language=language,
            segments=segments,
            duration_seconds=duration_seconds,
        )

    def _emit_configuration(
        self,
        *,
        model_name: str | None,
        device: str | None,
        compute_type: str | None,
        cpu_threads: int | None,
    ) -> None:
        """Emit the fixed local runtime configuration once per provider lifecycle."""

        if model_name is None or device is None or compute_type is None:
            return
        fields: dict[str, int | str] = {
            "model_name": model_name,
            "device": device,
            "compute_type": compute_type,
            "beam_size": self._beam_size,
            "vad": "on" if self._vad_enabled else "off",
            "execution_mode": "asyncio_to_thread",
            "cpu_threads_configured": "yes" if cpu_threads is not None else "no",
        }
        if cpu_threads is not None:
            fields["cpu_threads"] = cpu_threads
        emit_throughput("stt_configuration", **fields)

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


def _elapsed_milliseconds(started_at: float) -> int:
    """Return a privacy-safe monotonic duration for runtime diagnostics."""

    return int((time.monotonic() - started_at) * 1_000)
