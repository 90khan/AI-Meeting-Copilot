"""Offline-only Faster-Whisper Quality L prompt/context experiments.

This module is intentionally independent of the live provider, Assist, and
throughput paths.  It orchestrates deterministic chunk replay with an injected
transcriber, allowing tests to run without local model weights.
"""

from __future__ import annotations

import statistics
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.application.dto.ai import AudioInput, TranscriptionResult
from app.application.services.transcript_deduplicator import TranscriptDeduplicator
from app.core.throughput_diagnostics import estimate_v1_pcm16_wav_duration_ms
from app.development.quality_baseline import (
    VocabularyObservation,
    WordErrorRateReport,
    calculate_word_error_rate,
    normalize_german_for_wer,
)

_CONTEXT_SUFFIX_MAX_CHARACTERS = 320
_SHORT_CONTEXT_SUFFIX_MAX_CHARACTERS = 160
_VOCABULARY_PROMPT_MAX_CHARACTERS = 480
QUALITY_M_CRITICAL_VOCABULARY = (
    "EDEKA IT",
    "Adrian von Argo",
    "Heiko Flemming",
    "Gökhan Usluer",
    "RAG",
    "Agentic AI",
    "BM25",
    "HyDE",
    "Re-Ranking",
    "Hybrid Search",
    "Guardrails",
    "LLMOps",
    "Mörfelden-Walldorf",
)
_GERMAN_NUMBER_WORDS = frozenset(
    {
        "zwei",
        "drei",
        "vier",
        "fünf",
        "sechs",
        "sieben",
        "acht",
        "neun",
        "zehn",
        "zwanzig",
        "dreißig",
        "vierzig",
        "fünfzig",
        "sechzig",
        "siebzig",
        "achtzig",
        "neunzig",
        "hundert",
        "tausend",
    }
)


class SttExperimentVariant(StrEnum):
    """The four fixed Quality L conditions."""

    BASELINE = "baseline"
    VOCABULARY = "vocabulary"
    PREVIOUS_CONTEXT = "previous_context"
    VOCABULARY_AND_CONTEXT = "vocabulary_and_context"
    CRITICAL_VOCABULARY_AND_CONTEXT = "critical_vocabulary_and_context"
    CRITICAL_VOCABULARY_SHORT_CONTEXT = "critical_vocabulary_short_context"


@dataclass(frozen=True, slots=True, kw_only=True)
class SttExperimentDefinition:
    """Whether one fixed experiment adds only the named offline hint."""

    variant: SttExperimentVariant
    use_vocabulary: bool
    use_previous_context: bool
    vocabulary_terms: tuple[str, ...] | None = None
    context_max_characters: int = _CONTEXT_SUFFIX_MAX_CHARACTERS


EXPERIMENT_DEFINITIONS = (
    SttExperimentDefinition(
        variant=SttExperimentVariant.BASELINE,
        use_vocabulary=False,
        use_previous_context=False,
    ),
    SttExperimentDefinition(
        variant=SttExperimentVariant.VOCABULARY,
        use_vocabulary=True,
        use_previous_context=False,
    ),
    SttExperimentDefinition(
        variant=SttExperimentVariant.PREVIOUS_CONTEXT,
        use_vocabulary=False,
        use_previous_context=True,
    ),
    SttExperimentDefinition(
        variant=SttExperimentVariant.VOCABULARY_AND_CONTEXT,
        use_vocabulary=True,
        use_previous_context=True,
    ),
)

QUALITY_M_EXPERIMENT_DEFINITIONS = (
    EXPERIMENT_DEFINITIONS[2],
    EXPERIMENT_DEFINITIONS[3],
    SttExperimentDefinition(
        variant=SttExperimentVariant.CRITICAL_VOCABULARY_AND_CONTEXT,
        use_vocabulary=True,
        use_previous_context=True,
        vocabulary_terms=QUALITY_M_CRITICAL_VOCABULARY,
    ),
    SttExperimentDefinition(
        variant=SttExperimentVariant.CRITICAL_VOCABULARY_SHORT_CONTEXT,
        use_vocabulary=True,
        use_previous_context=True,
        vocabulary_terms=QUALITY_M_CRITICAL_VOCABULARY,
        context_max_characters=_SHORT_CONTEXT_SUFFIX_MAX_CHARACTERS,
    ),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class OfflineTranscription:
    """One completed local transcription and private-run timing metadata."""

    result: TranscriptionResult
    elapsed_ms: int
    model_load_ms: int
    model_loaded: bool
    process_rss_bytes: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericFactObservation:
    """Presence-only numeric/duration fact check for offline inspection."""

    token: str
    observed_present: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkMeasurement:
    """One replay chunk; text remains in the private report only."""

    chunk_index: int
    audio_duration_ms: int
    elapsed_ms: int
    model_load_ms: int
    model_loaded: bool
    process_rss_bytes: int | None
    initial_prompt_kind: str
    prompt_characters: int
    vocabulary_prompt_characters: int
    context_characters: int
    raw_text: str
    accepted_text: str | None
    deduplicated_segment_count: int

    @property
    def rtf(self) -> float:
        """Return processing seconds per audio second for this chunk."""

        if self.audio_duration_ms == 0:
            return 0.0
        return self.elapsed_ms / self.audio_duration_ms


@dataclass(frozen=True, slots=True, kw_only=True)
class LatencySummary:
    """Deterministic latency summary for one bounded replay."""

    minimum_ms: int
    median_ms: int
    p95_ms: int
    maximum_ms: int
    total_processing_ms: int
    total_audio_ms: int
    rtf_median: float
    rtf_p95: float
    rtf_maximum: float

    @property
    def rtf(self) -> float:
        """Return total replay processing time divided by audio duration."""

        if self.total_audio_ms == 0:
            return 0.0
        return self.total_processing_ms / self.total_audio_ms


@dataclass(frozen=True, slots=True, kw_only=True)
class ColdWarmLatencySummary:
    """Separate one model-load/first-chunk observation from warm chunk timing."""

    model_load_ms: int
    first_chunk_latency_ms: int
    process_rss_bytes: int | None
    warm: LatencySummary


@dataclass(frozen=True, slots=True, kw_only=True)
class SttExperimentResult:
    """All private Quality L evidence for one fixed condition."""

    definition: SttExperimentDefinition
    observed_text: str
    wer: WordErrorRateReport
    vocabulary: tuple[VocabularyObservation, ...]
    numeric_facts: tuple[NumericFactObservation, ...]
    chunks: tuple[ChunkMeasurement, ...]
    latency: LatencySummary

    @property
    def deduplicated_segment_count(self) -> int:
        """Return aggregate removals from the existing live deduplicator."""

        return sum(chunk.deduplicated_segment_count for chunk in self.chunks)

    @property
    def cold_warm_latency(self) -> ColdWarmLatencySummary:
        """Return cold load/first inference separately from warm replay timing."""

        first_chunk, *warm_chunks = self.chunks
        return ColdWarmLatencySummary(
            model_load_ms=first_chunk.model_load_ms,
            first_chunk_latency_ms=first_chunk.elapsed_ms,
            process_rss_bytes=first_chunk.process_rss_bytes,
            warm=_summarize_latency(warm_chunks),
        )


ChunkTranscriber = Callable[[AudioInput, str | None], Awaitable[OfflineTranscription]]


async def run_stt_experiments(
    *,
    reference_de: str,
    audio_chunks: Iterable[AudioInput],
    evaluation_terms: Iterable[str],
    transcribe: ChunkTranscriber,
    definitions: Iterable[SttExperimentDefinition] | None = None,
) -> tuple[SttExperimentResult, ...]:
    """Replay fixed V1 chunks through A/B/C/D without changing live behavior."""

    chunks = tuple(audio_chunks)
    if not chunks:
        raise ValueError("Quality L requires at least one complete live chunk.")
    terms = tuple(term for term in evaluation_terms if term.strip())
    results: list[SttExperimentResult] = []
    for definition in definitions or EXPERIMENT_DEFINITIONS:
        results.append(
            await _run_one_experiment(
                definition=definition,
                reference_de=reference_de,
                audio_chunks=chunks,
                evaluation_terms=terms,
                transcribe=transcribe,
            )
        )
    return tuple(results)


async def _run_one_experiment(
    *,
    definition: SttExperimentDefinition,
    reference_de: str,
    audio_chunks: tuple[AudioInput, ...],
    evaluation_terms: tuple[str, ...],
    transcribe: ChunkTranscriber,
) -> SttExperimentResult:
    deduplicator = TranscriptDeduplicator()
    previous_accepted_segment: str | None = None
    previous_accepted_chunk: str | None = None
    accepted_segments: list[str] = []
    measurements: list[ChunkMeasurement] = []
    for index, audio in enumerate(audio_chunks, start=1):
        prompt = build_initial_prompt(
            definition=definition,
            evaluation_terms=evaluation_terms,
            previous_accepted_chunk=previous_accepted_chunk,
        )
        transcription = await transcribe(audio, prompt)
        accepted_in_chunk: list[str] = []
        deduplicated_count = 0
        raw_segments = tuple(segment.text for segment in transcription.result.segments)
        for raw_segment in raw_segments:
            accepted = deduplicator.deduplicate(
                previous_text=previous_accepted_segment,
                current_text=raw_segment,
            )
            if accepted is None:
                deduplicated_count += 1
                continue
            accepted_segments.append(accepted)
            accepted_in_chunk.append(accepted)
            previous_accepted_segment = accepted
        accepted_chunk = " ".join(accepted_in_chunk) or None
        if accepted_chunk is not None:
            previous_accepted_chunk = accepted_chunk
        measurements.append(
            ChunkMeasurement(
                chunk_index=index,
                audio_duration_ms=_audio_duration_ms(audio),
                elapsed_ms=transcription.elapsed_ms,
                model_load_ms=transcription.model_load_ms,
                model_loaded=transcription.model_loaded,
                process_rss_bytes=transcription.process_rss_bytes,
                initial_prompt_kind=_prompt_kind(prompt),
                prompt_characters=len(prompt or ""),
                vocabulary_prompt_characters=_vocabulary_prompt_length(prompt),
                context_characters=_context_prompt_length(prompt),
                raw_text=" ".join(raw_segments),
                accepted_text=accepted_chunk,
                deduplicated_segment_count=deduplicated_count,
            )
        )
    observed = " ".join(accepted_segments)
    vocabulary = tuple(
        VocabularyObservation(
            term=term,
            expected_present=_phrase_present(term, reference_de),
            observed_present=_phrase_present(term, observed),
        )
        for term in evaluation_terms
    )
    return SttExperimentResult(
        definition=definition,
        observed_text=observed,
        wer=calculate_word_error_rate(reference_de, observed),
        vocabulary=vocabulary,
        numeric_facts=tuple(
            NumericFactObservation(
                token=token,
                observed_present=token in normalize_german_for_wer(observed),
            )
            for token in _numeric_reference_tokens(reference_de)
        ),
        chunks=tuple(measurements),
        latency=_summarize_latency(measurements),
    )


def build_initial_prompt(
    *,
    definition: SttExperimentDefinition,
    evaluation_terms: Iterable[str],
    previous_accepted_chunk: str | None,
) -> str | None:
    """Build a deterministic bounded offline-only Faster-Whisper prompt."""

    components: list[str] = []
    if definition.use_vocabulary:
        vocabulary = _bounded_vocabulary_prompt(
            definition.vocabulary_terms or evaluation_terms
        )
        if vocabulary:
            components.append(f"German technical vocabulary: {vocabulary}")
    if definition.use_previous_context and previous_accepted_chunk:
        context = _bounded_suffix(
            previous_accepted_chunk,
            definition.context_max_characters,
        )
        components.append("Previous accepted German context: " f"{context}")
    return "\n".join(components) or None


def render_stt_quality_report(results: Iterable[SttExperimentResult]) -> str:
    """Render explicitly supplied private text and objective replay metrics."""

    lines = ["QUALITY L — Offline Faster-Whisper Prompt/Context Replay", "---"]
    for result in results:
        latency = result.latency
        cold_warm = result.cold_warm_latency
        lines.extend(
            [
                "",
                f"Variant: {result.definition.variant.value}",
                f"WER: {result.wer.wer:.3f}",
                f"Substitutions: {result.wer.substitutions}",
                f"Deletions: {result.wer.deletions}",
                f"Insertions: {result.wer.insertions}",
                f"Reference words: {result.wer.reference_word_count}",
                "Latency ms: "
                f"min={latency.minimum_ms} median={latency.median_ms} "
                f"p95={latency.p95_ms} max={latency.maximum_ms}",
                f"Total RTF: {latency.rtf:.3f}",
                "Chunk RTF: "
                f"median={latency.rtf_median:.3f} p95={latency.rtf_p95:.3f} "
                f"max={latency.rtf_maximum:.3f}",
                "Tail chunks: "
                + " ".join(
                    f">{threshold}ms={_tail_count(result.chunks, threshold)}/"
                    f"{len(result.chunks)}"
                    for threshold in (4_000, 8_000, 15_000, 30_000)
                ),
                f"Deduplicated segments: {result.deduplicated_segment_count}",
                "Cold: "
                f"model_load_ms={cold_warm.model_load_ms} "
                f"first_chunk_latency_ms={cold_warm.first_chunk_latency_ms} "
                f"process_rss_bytes={cold_warm.process_rss_bytes}",
                "Warm chunks (excluding first): "
                f"latency_ms=min={cold_warm.warm.minimum_ms} "
                f"median={cold_warm.warm.median_ms} "
                f"p95={cold_warm.warm.p95_ms} "
                f"max={cold_warm.warm.maximum_ms} "
                f"rtf=median={cold_warm.warm.rtf_median:.3f} "
                f"p95={cold_warm.warm.rtf_p95:.3f} "
                f"max={cold_warm.warm.rtf_maximum:.3f}",
                "Observed German: " + result.observed_text,
                "Vocabulary: "
                + ", ".join(
                    f"{item.term}={'yes' if item.observed_present else 'no'}"
                    for item in result.vocabulary
                    if item.expected_present
                ),
                "Numeric/duration tokens: "
                + ", ".join(
                    f"{item.token}={'yes' if item.observed_present else 'no'}"
                    for item in result.numeric_facts
                ),
            ]
        )
        for chunk in result.chunks:
            lines.append(
                "Chunk "
                f"{chunk.chunk_index}: elapsed_ms={chunk.elapsed_ms} "
                f"audio_ms={chunk.audio_duration_ms} rtf={chunk.rtf:.3f} "
                f"model_loaded={'yes' if chunk.model_loaded else 'no'} "
                f"model_load_ms={chunk.model_load_ms} "
                f"process_rss_bytes={chunk.process_rss_bytes} "
                f"prompt={chunk.initial_prompt_kind} chars={chunk.prompt_characters} "
                f"vocabulary_chars={chunk.vocabulary_prompt_characters} "
                f"context_chars={chunk.context_characters} "
                f"deduplicated_segments={chunk.deduplicated_segment_count}"
            )
    return "\n".join(lines) + "\n"


def _bounded_vocabulary_prompt(terms: Iterable[str]) -> str:
    selected: list[str] = []
    length = 0
    for term in terms:
        candidate = term.strip()
        if not candidate:
            continue
        separator = 2 if selected else 0
        if length + separator + len(candidate) > _VOCABULARY_PROMPT_MAX_CHARACTERS:
            break
        selected.append(candidate)
        length += separator + len(candidate)
    return "; ".join(selected)


def _bounded_suffix(text: str, maximum_characters: int) -> str:
    if len(text) <= maximum_characters:
        return text
    suffix = text[-maximum_characters:]
    separator = suffix.find(" ")
    return suffix[separator + 1 :] if separator >= 0 else suffix


def _prompt_kind(prompt: str | None) -> str:
    if prompt is None:
        return "none"
    has_vocabulary = prompt.startswith("German technical vocabulary:")
    has_context = "Previous accepted German context:" in prompt
    if has_vocabulary and has_context:
        return "vocabulary_and_context"
    return "vocabulary" if has_vocabulary else "previous_context"


def _vocabulary_prompt_length(prompt: str | None) -> int:
    if prompt is None or not prompt.startswith("German technical vocabulary:"):
        return 0
    return len(prompt.split("\n", maxsplit=1)[0])


def _context_prompt_length(prompt: str | None) -> int:
    if prompt is None or "Previous accepted German context: " not in prompt:
        return 0
    return len(prompt.rsplit("Previous accepted German context: ", maxsplit=1)[1])


def _audio_duration_ms(audio: AudioInput) -> int:
    return estimate_v1_pcm16_wav_duration_ms(
        byte_length=len(audio.data),
        sample_rate_hz=audio.sample_rate_hz,
        channels=audio.channels,
    )


def _summarize_latency(chunks: Iterable[ChunkMeasurement]) -> LatencySummary:
    resolved = tuple(chunks)
    values = sorted(chunk.elapsed_ms for chunk in resolved)
    if not values:
        raise ValueError("Quality L requires at least one chunk measurement.")
    p95_index = max(0, math_ceil_divide(len(values) * 95, 100) - 1)
    rtfs = sorted(chunk.rtf for chunk in resolved)
    return LatencySummary(
        minimum_ms=values[0],
        median_ms=int(statistics.median(values)),
        p95_ms=values[p95_index],
        maximum_ms=values[-1],
        total_processing_ms=sum(values),
        total_audio_ms=sum(chunk.audio_duration_ms for chunk in resolved),
        rtf_median=statistics.median(rtfs),
        rtf_p95=rtfs[p95_index],
        rtf_maximum=rtfs[-1],
    )


def math_ceil_divide(numerator: int, denominator: int) -> int:
    """Return a positive integer ceiling without floating-point rounding."""

    return -(-numerator // denominator)


def _phrase_present(phrase: str, text: str) -> bool:
    normalized_phrase = normalize_german_for_wer(phrase)
    normalized_text = normalize_german_for_wer(text)
    if not normalized_phrase:
        return False
    width = len(normalized_phrase)
    return any(
        normalized_text[index : index + width] == normalized_phrase
        for index in range(len(normalized_text) - width + 1)
    )


def _numeric_reference_tokens(text: str) -> tuple[str, ...]:
    tokens = normalize_german_for_wer(text)
    return tuple(
        dict.fromkeys(
            token
            for token in tokens
            if token.isdecimal() or token in _GERMAN_NUMBER_WORDS
        )
    )


def _tail_count(chunks: Iterable[ChunkMeasurement], threshold_ms: int) -> int:
    return sum(chunk.elapsed_ms > threshold_ms for chunk in chunks)
