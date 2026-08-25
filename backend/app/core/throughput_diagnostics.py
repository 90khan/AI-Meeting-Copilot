"""Opt-in, privacy-safe live-transcription throughput diagnostics."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Final

from app.core.logging import get_logger

_LOGGER = get_logger("app.throughput")
_ENABLED = False
_OLLAMA_ACTIVE_REQUESTS = 0
_OLLAMA_ACTIVE_REQUESTS_LOCK = Lock()
_CURRENT_CHUNK_SEQUENCE: ContextVar[int | None] = ContextVar(
    "amcp_current_chunk_sequence",
    default=None,
)

_STAGES: Final[frozenset[str]] = frozenset(
    {
        "session_started",
        "chunk_received",
        "backend_chunk",
        "backend_chunk_completed",
        "stt_started",
        "stt_completed",
        "postprocess_completed",
        "chunk_result_sent",
        "stt_provider_started",
        "stt_worker_started",
        "stt_worker_completed",
        "stt_model_ready",
        "stt_audio_decode_conversion_completed",
        "stt_inference_completed",
        "stt_transcription_completed",
        "stt_configuration",
        "assist_enqueued",
        "assist_dequeued",
        "assist_outer_queue_coalesced",
        "assist_capability_started",
        "assist_capability_completed",
        "assist_segment_completed",
        "assist_translation_started",
        "assist_translation_provider_resolved",
        "assist_translation_provider_call_started",
        "assist_translation_provider_call_failed",
        "assist_translation_response_received",
        "assist_translation_response_validation_failed",
        "assist_translation_completed",
        "assist_translation_update_ready",
        "assist_translation_update_failed",
        "assist_translation_scheduler",
        "ollama_started",
        "ollama_response_received",
        "ollama_response_validation_failed",
        "ollama_completed",
    }
)
_INTEGER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "sequence",
        "ordinal",
        "elapsed_ms",
        "queue_wait_ms",
        "queue_depth",
        "replaced_count",
        "accepted_count",
        "active_requests",
        "ollama_active_requests",
        "audio_duration_ms",
        "processed_audio_ms",
        "frame_parse_ms",
        "model_load_ms",
        "audio_decode_conversion_ms",
        "stt_inference_ms",
        "backend_total_ms",
        "entry_monotonic_ms",
        "backend_queue_wait_ms",
        "preprocessing_ms",
        "stt_duration_ms",
        "post_processing_ms",
        "total_backend_ms",
        "stt_rtf_milli",
        "beam_size",
        "cpu_threads",
        "active_translation_count",
        "pending_translation_count",
        "upstream_pending_stt_chunks",
        "estimated_idle_window_ms",
        "estimated_translation_duration_ms",
        "extended_idle_margin_ms",
    }
)
_ENUM_FIELDS: Final[dict[str, frozenset[str]]] = {
    "assist": frozenset({"on", "off"}),
    "translation": frozenset({"on", "off"}),
    "simplification": frozenset({"on", "off"}),
    "reply_coaching": frozenset({"on", "off"}),
    "capability": frozenset({"translation", "simplification", "reply_coaching"}),
    "outcome": frozenset({"completed", "failed", "cancelled", "ready", "unavailable"}),
    "first_request": frozenset({"yes", "no"}),
    "model_loaded": frozenset({"yes", "no"}),
    "model_name": frozenset(
        {
            "tiny",
            "tiny.en",
            "base",
            "base.en",
            "small",
            "small.en",
            "medium",
            "medium.en",
            "large-v1",
            "large-v2",
            "large-v3",
            "turbo",
        }
    ),
    "device": frozenset({"cpu", "cuda", "auto"}),
    "compute_type": frozenset(
        {"auto", "default", "int8", "int8_float16", "int16", "float16", "float32"}
    ),
    "vad": frozenset({"on", "off"}),
    "execution_mode": frozenset({"asyncio_to_thread"}),
    "cpu_threads_configured": frozenset({"yes", "no"}),
    "state": frozenset(
        {
            "started",
            "busy",
            "pending",
            "deferred",
            "coalesced",
            "completed",
            "cancelled",
        }
    ),
    "admission_reason": frozenset(
        {"probe", "predicted_window", "extended_idle", "upstream_backlog"}
    ),
    "reason": frozenset(
        {
            "connection_failed",
            "timeout",
            "http_status_error",
            "request_failed",
            "model_unavailable",
            "malformed_response",
            "empty_response",
            "response_validation_failed",
            "provider_internal_error",
        }
    ),
}

_V1_WAV_HEADER_BYTES = 44
_PCM16_BYTES_PER_SAMPLE = 2


def configure_throughput_diagnostics(*, enabled: bool) -> None:
    """Enable or disable the explicitly opt-in diagnostic stream."""

    global _ENABLED, _OLLAMA_ACTIVE_REQUESTS
    _ENABLED = enabled
    if not enabled:
        with _OLLAMA_ACTIVE_REQUESTS_LOCK:
            _OLLAMA_ACTIVE_REQUESTS = 0


def begin_ollama_request() -> int:
    """Track one active local Ollama request using only a bounded count."""

    if not _ENABLED:
        return 0
    global _OLLAMA_ACTIVE_REQUESTS
    with _OLLAMA_ACTIVE_REQUESTS_LOCK:
        _OLLAMA_ACTIVE_REQUESTS += 1
        return _OLLAMA_ACTIVE_REQUESTS


def finish_ollama_request() -> int:
    """Release one tracked Ollama request without retaining request content."""

    if not _ENABLED:
        return 0
    global _OLLAMA_ACTIVE_REQUESTS
    with _OLLAMA_ACTIVE_REQUESTS_LOCK:
        _OLLAMA_ACTIVE_REQUESTS = max(0, _OLLAMA_ACTIVE_REQUESTS - 1)
        return _OLLAMA_ACTIVE_REQUESTS


def active_ollama_requests() -> int:
    """Return the current opt-in Ollama activity count for STT correlation."""

    if not _ENABLED:
        return 0
    with _OLLAMA_ACTIVE_REQUESTS_LOCK:
        return _OLLAMA_ACTIVE_REQUESTS


@contextmanager
def throughput_chunk_sequence(sequence: int) -> Iterator[None]:
    """Associate opt-in provider metrics with one validated chunk sequence.

    ``asyncio.to_thread`` copies context variables into the worker, letting the
    provider emit the same non-sensitive sequence as the WebSocket boundary
    without changing speech DTOs or provider semantics.
    """

    token = _CURRENT_CHUNK_SEQUENCE.set(sequence)
    try:
        yield
    finally:
        _CURRENT_CHUNK_SEQUENCE.reset(token)


def current_throughput_chunk_sequence() -> int | None:
    """Return the active diagnostic-only sequence, if a chunk is being processed."""

    return _CURRENT_CHUNK_SEQUENCE.get()


def current_sequence_fields() -> dict[str, int]:
    """Return the current sequence as a safe metric field when available."""

    sequence = current_throughput_chunk_sequence()
    return {} if sequence is None else {"sequence": sequence}


def estimate_v1_pcm16_wav_duration_ms(
    *,
    byte_length: int,
    sample_rate_hz: int,
    channels: int,
) -> int:
    """Estimate V1 WAV duration from validated structural audio metadata only.

    V1 sends PCM16 mono WAV with its fixed 44-byte RIFF header. This helper
    never inspects audio bytes and returns zero for values that cannot describe
    a valid PCM16 duration; the result is diagnostic-only.
    """

    if (
        type(byte_length) is not int
        or type(sample_rate_hz) is not int
        or type(channels) is not int
        or byte_length < _V1_WAV_HEADER_BYTES
        or sample_rate_hz <= 0
        or channels <= 0
    ):
        return 0
    bytes_per_second = sample_rate_hz * channels * _PCM16_BYTES_PER_SAMPLE
    return (byte_length - _V1_WAV_HEADER_BYTES) * 1_000 // bytes_per_second


def estimate_processed_audio_ms(
    *,
    audio_duration_ms: int,
    overlap_seconds: float,
    is_first_chunk: bool,
) -> int:
    """Return non-overlap audio represented by one finalized V1 chunk."""

    if type(audio_duration_ms) is not int or audio_duration_ms < 0:
        return 0
    if is_first_chunk:
        return audio_duration_ms
    if (
        not isinstance(overlap_seconds, (int, float))
        or isinstance(overlap_seconds, bool)
        or not math.isfinite(overlap_seconds)
    ):
        return 0
    overlap_ms = int(overlap_seconds * 1_000)
    if overlap_ms < 0:
        return 0
    return max(0, audio_duration_ms - overlap_ms)


def emit_throughput(stage: str, /, **fields: int | str) -> None:
    """Emit one fixed-schema metric line without any user-provided content.

    Invalid internal calls are ignored deliberately: observability must never
    alter a live transcription session's control flow.
    """

    if not _ENABLED or not _LOGGER.isEnabledFor(logging.INFO):
        return
    if stage not in _STAGES or not _fields_are_safe(fields):
        return

    values = [f"stage={stage}"]
    values.extend(f"{field_name}={value}" for field_name, value in fields.items())
    _LOGGER.info("amcp-throughput %s", " ".join(values))


def _fields_are_safe(fields: dict[str, int | str]) -> bool:
    """Require every emitted value to be a closed, non-sensitive primitive."""

    for field_name, value in fields.items():
        if field_name in _INTEGER_FIELDS:
            if type(value) is not int or value < 0:
                return False
            continue
        allowed_values = _ENUM_FIELDS.get(field_name)
        if allowed_values is None or not isinstance(value, str):
            return False
        if value not in allowed_values:
            return False
    return True
