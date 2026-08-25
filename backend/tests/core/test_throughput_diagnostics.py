"""Tests for the opt-in, content-free throughput diagnostic stream."""

import logging

import pytest
from app.core.throughput_diagnostics import (
    active_ollama_requests,
    begin_ollama_request,
    configure_throughput_diagnostics,
    emit_throughput,
    estimate_processed_audio_ms,
    estimate_v1_pcm16_wav_duration_ms,
    finish_ollama_request,
)


def test_opt_in_metric_uses_only_the_closed_safe_schema(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A valid metric has only fixed labels and non-sensitive numeric fields."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput(
                "stt_completed",
                sequence=12,
                elapsed_ms=345,
            )
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert [record.getMessage() for record in caplog.records] == [
        "amcp-throughput stage=stt_completed sequence=12 elapsed_ms=345"
    ]


def test_sensitive_or_malformed_values_are_never_emitted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The diagnostic boundary rejects content fields and unsafe values."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput("stt_completed", sequence=1, text="private speech")
            emit_throughput("stt_completed", sequence=-1, elapsed_ms=1)
            emit_throughput("unknown_stage", sequence=1)
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert caplog.records == []


def test_first_request_and_model_load_metrics_remain_closed_and_structural(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cold-start timing labels cannot carry model, audio, or user content."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput(
                "stt_model_ready",
                ordinal=1,
                model_load_ms=14_000,
                model_loaded="yes",
                first_request="yes",
            )
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert [record.getMessage() for record in caplog.records] == [
        "amcp-throughput stage=stt_model_ready ordinal=1 model_load_ms=14000 "
        "model_loaded=yes first_request=yes"
    ]


def test_assist_translation_failure_reasons_remain_closed_and_structural(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ollama failure metrics accept only the fixed privacy-safe reason set."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput(
                "ollama_completed",
                outcome="failed",
                elapsed_ms=125,
                reason="model_unavailable",
            )
            emit_throughput(
                "ollama_completed",
                outcome="failed",
                elapsed_ms=125,
                reason="raw provider response",
            )
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert [record.getMessage() for record in caplog.records] == [
        "amcp-throughput stage=ollama_completed outcome=failed elapsed_ms=125 "
        "reason=model_unavailable"
    ]


def test_stt_runtime_configuration_uses_only_allowlisted_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Startup configuration excludes paths and arbitrary model identifiers."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput(
                "stt_configuration",
                model_name="small",
                device="cpu",
                compute_type="int8",
                beam_size=5,
                vad="off",
                execution_mode="asyncio_to_thread",
                cpu_threads_configured="no",
            )
            emit_throughput(
                "stt_configuration",
                model_name="/private/model-path",
                device="cpu",
                compute_type="int8",
                beam_size=5,
                vad="off",
                execution_mode="asyncio_to_thread",
                cpu_threads_configured="no",
            )
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert len(caplog.records) == 1
    assert "model_name=small" in caplog.records[0].getMessage()
    assert "/private/model-path" not in caplog.records[0].getMessage()


def test_translation_scheduler_metrics_are_bounded_and_content_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scheduler state uses fixed labels and small numeric counters only."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput(
                "assist_translation_scheduler",
                state="deferred",
                active_translation_count=0,
                pending_translation_count=1,
                upstream_pending_stt_chunks=3,
                admission_reason="upstream_backlog",
                estimated_idle_window_ms=900,
                estimated_translation_duration_ms=1_500,
                extended_idle_margin_ms=250,
            )
            emit_throughput(
                "assist_translation_scheduler",
                state="private transcript",
                active_translation_count=1,
                pending_translation_count=1,
            )
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert [record.getMessage() for record in caplog.records] == [
        "amcp-throughput stage=assist_translation_scheduler state=deferred "
        "active_translation_count=0 pending_translation_count=1 "
        "upstream_pending_stt_chunks=3 admission_reason=upstream_backlog "
        "estimated_idle_window_ms=900 "
        "estimated_translation_duration_ms=1500 extended_idle_margin_ms=250"
    ]


def test_translation_only_outer_queue_coalescing_metrics_are_content_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Superseding stale translation work emits only closed structural fields."""

    configure_throughput_diagnostics(enabled=True)
    try:
        with caplog.at_level(logging.INFO, logger="app.throughput"):
            emit_throughput(
                "assist_outer_queue_coalesced",
                capability="translation",
                replaced_count=1,
                queue_depth=1,
            )
            emit_throughput(
                "assist_outer_queue_coalesced",
                capability="private transcript",
                replaced_count=1,
                queue_depth=1,
            )
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert [record.getMessage() for record in caplog.records] == [
        "amcp-throughput stage=assist_outer_queue_coalesced "
        "capability=translation replaced_count=1 queue_depth=1"
    ]


def test_audio_duration_estimates_use_only_v1_pcm_structure() -> None:
    """The throughput rate uses no audio content or timestamp data."""

    audio_duration_ms = estimate_v1_pcm16_wav_duration_ms(
        byte_length=44 + 16_000 * 2 * 4,
        sample_rate_hz=16_000,
        channels=1,
    )

    assert audio_duration_ms == 4_000
    assert (
        estimate_processed_audio_ms(
            audio_duration_ms=audio_duration_ms,
            overlap_seconds=1.0,
            is_first_chunk=True,
        )
        == 4_000
    )
    assert (
        estimate_processed_audio_ms(
            audio_duration_ms=audio_duration_ms,
            overlap_seconds=1.0,
            is_first_chunk=False,
        )
        == 3_000
    )
    assert (
        estimate_v1_pcm16_wav_duration_ms(
            byte_length=43,
            sample_rate_hz=16_000,
            channels=1,
        )
        == 0
    )


def test_ollama_activity_is_a_bounded_content_free_counter() -> None:
    """STT correlation observes only the current number of local requests."""

    configure_throughput_diagnostics(enabled=True)
    try:
        assert begin_ollama_request() == 1
        assert begin_ollama_request() == 2
        assert active_ollama_requests() == 2
        assert finish_ollama_request() == 1
        assert finish_ollama_request() == 0
    finally:
        configure_throughput_diagnostics(enabled=False)
