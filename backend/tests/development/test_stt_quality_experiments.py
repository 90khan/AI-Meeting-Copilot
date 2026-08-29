"""Tests for the offline-only Quality L STT experiment runner."""

from __future__ import annotations

import asyncio

from app.application.dto.ai import (
    AudioFormat,
    AudioInput,
    LanguageCode,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.development.stt_quality_experiments import (
    EXPERIMENT_DEFINITIONS,
    QUALITY_M_CRITICAL_VOCABULARY,
    QUALITY_M_EXPERIMENT_DEFINITIONS,
    OfflineTranscription,
    SttExperimentVariant,
    build_initial_prompt,
    run_stt_experiments,
)


def _audio() -> AudioInput:
    return AudioInput(
        data=b"R" * (44 + 16_000 * 4 * 2),
        sample_rate_hz=16_000,
        channels=1,
        audio_format=AudioFormat.WAV,
    )


def _transcription(text: str, elapsed_ms: int) -> OfflineTranscription:
    return OfflineTranscription(
        result=TranscriptionResult(
            language=LanguageCode(value="de"),
            segments=(
                TranscriptionSegment(
                    text=text,
                    start_seconds=0.0,
                    end_seconds=4.0,
                ),
            ),
            duration_seconds=4.0,
        ),
        elapsed_ms=elapsed_ms,
        model_load_ms=0,
        model_loaded=False,
    )


def test_fixed_variants_change_only_the_explicit_offline_prompt() -> None:
    async def run() -> None:
        prompts: list[str | None] = []
        calls = 0

        async def transcribe(_: AudioInput, prompt: str | None) -> OfflineTranscription:
            nonlocal calls
            prompts.append(prompt)
            calls += 1
            return _transcription(
                "EDEKA IT arbeitet mit RAG." if calls % 2 else "RAG geht weiter.",
                1_000,
            )

        results = await run_stt_experiments(
            reference_de="EDEKA IT arbeitet mit RAG. RAG geht weiter.",
            audio_chunks=(_audio(), _audio()),
            evaluation_terms=("EDEKA IT", "RAG"),
            transcribe=transcribe,
        )

        assert [item.definition.variant for item in results] == [
            SttExperimentVariant.BASELINE,
            SttExperimentVariant.VOCABULARY,
            SttExperimentVariant.PREVIOUS_CONTEXT,
            SttExperimentVariant.VOCABULARY_AND_CONTEXT,
        ]
        assert prompts[0:2] == [None, None]
        assert prompts[2:4] == [
            "German technical vocabulary: EDEKA IT; RAG",
            "German technical vocabulary: EDEKA IT; RAG",
        ]
        assert prompts[4] is None
        assert prompts[5] == (
            "Previous accepted German context: EDEKA IT arbeitet mit RAG."
        )
        assert prompts[6] == "German technical vocabulary: EDEKA IT; RAG"
        assert prompts[7] == (
            "German technical vocabulary: EDEKA IT; RAG\n"
            "Previous accepted German context: EDEKA IT arbeitet mit RAG."
        )

    asyncio.run(run())


def test_experiment_uses_live_deduplication_and_reports_latency_and_terms() -> None:
    async def run() -> None:
        responses = iter(
            (
                _transcription("Hallo EDEKA IT", 1_000),
                _transcription("EDEKA IT mit RAG", 2_000),
            )
            * 4
        )

        async def transcribe(_: AudioInput, __: str | None) -> OfflineTranscription:
            return next(responses)

        baseline, *_ = await run_stt_experiments(
            reference_de="Hallo EDEKA IT mit RAG zwei",
            audio_chunks=(_audio(), _audio()),
            evaluation_terms=("EDEKA IT", "RAG"),
            transcribe=transcribe,
        )

        assert baseline.observed_text == "Hallo EDEKA IT mit RAG"
        assert baseline.chunks[1].deduplicated_segment_count == 0
        assert baseline.chunks[1].accepted_text == "mit RAG"
        assert baseline.latency.minimum_ms == 1_000
        assert baseline.latency.median_ms == 1_500
        assert baseline.latency.p95_ms == 2_000
        assert baseline.latency.rtf == 0.375
        assert baseline.cold_warm_latency.model_load_ms == 0
        assert baseline.cold_warm_latency.first_chunk_latency_ms == 1_000
        assert baseline.cold_warm_latency.process_rss_bytes is None
        assert baseline.cold_warm_latency.warm.minimum_ms == 2_000
        assert baseline.cold_warm_latency.warm.rtf == 0.5
        assert [item.observed_present for item in baseline.vocabulary] == [True, True]
        assert [
            (item.token, item.observed_present) for item in baseline.numeric_facts
        ] == [("zwei", False)]

    asyncio.run(run())


def test_initial_prompt_is_bounded_and_never_includes_prior_full_history() -> None:
    # The public runner covers the four definitions. This direct assertion keeps
    # the bounded-context boundary explicit without any model dependency.
    context_definition = EXPERIMENT_DEFINITIONS[2]
    prompt = build_initial_prompt(
        definition=context_definition,
        evaluation_terms=(),
        previous_accepted_chunk="word " * 200,
    )

    assert prompt is not None
    assert len(prompt) < 400
    assert prompt.startswith("Previous accepted German context: ")


def test_experiment_retains_measurements_for_empty_or_fully_deduplicated_chunks() -> (
    None
):
    async def run() -> None:
        responses = iter(
            (
                _transcription("EDEKA IT", 1_000),
                OfflineTranscription(
                    result=TranscriptionResult(
                        language=LanguageCode(value="de"),
                        segments=(),
                        duration_seconds=4.0,
                    ),
                    elapsed_ms=2_000,
                    model_load_ms=0,
                    model_loaded=False,
                ),
                _transcription("EDEKA IT", 3_000),
            )
            * 4
        )

        async def transcribe(_: AudioInput, __: str | None) -> OfflineTranscription:
            return next(responses)

        baseline, *_ = await run_stt_experiments(
            reference_de="EDEKA IT",
            audio_chunks=(_audio(), _audio(), _audio()),
            evaluation_terms=("EDEKA IT",),
            transcribe=transcribe,
        )

        assert len(baseline.chunks) == 3
        assert baseline.chunks[1].accepted_text is None
        assert baseline.chunks[2].accepted_text is None
        assert baseline.chunks[2].deduplicated_segment_count == 1
        assert baseline.latency.maximum_ms == 3_000

    asyncio.run(run())


def test_quality_m_uses_the_fixed_critical_list_and_shorter_context() -> None:
    critical_definition, short_definition = QUALITY_M_EXPERIMENT_DEFINITIONS[-2:]
    long_context = "word " * 100

    critical_prompt = build_initial_prompt(
        definition=critical_definition,
        evaluation_terms=("unrelated",),
        previous_accepted_chunk=long_context,
    )
    short_prompt = build_initial_prompt(
        definition=short_definition,
        evaluation_terms=("unrelated",),
        previous_accepted_chunk=long_context,
    )

    assert critical_prompt is not None
    assert short_prompt is not None
    assert all(term in critical_prompt for term in QUALITY_M_CRITICAL_VOCABULARY)
    assert "unrelated" not in critical_prompt
    assert len(short_prompt) < len(critical_prompt)
    short_context = short_prompt.rsplit("Previous accepted German context: ", 1)[1]
    long_context = critical_prompt.rsplit("Previous accepted German context: ", 1)[1]
    assert len(short_context) <= 160
    assert len(long_context) <= 320
