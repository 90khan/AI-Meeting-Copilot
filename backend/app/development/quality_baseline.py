"""Offline-only baseline evaluation for local STT and translation quality.

This module intentionally depends only on application provider interfaces.  It
is not imported by the WebSocket, Assist, or runtime telemetry paths.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.application.dto.ai import (
    AudioInput,
    LanguageCode,
    SpeechToTextRequest,
    TranslationRequest,
)
from app.application.exceptions import ProviderError, ProviderTimeoutError
from app.application.interfaces.ai import SpeechToTextProvider, TranslationProvider
from app.application.services.transcript_deduplicator import TranscriptDeduplicator

_LIVE_CHUNK_SECONDS = 4
_LIVE_CHUNK_OVERLAP_SECONDS = 1
_COHERENT_TRANSLATION_UNIT_MAX_CHARACTERS = 1_000
_DEFAULT_OFFLINE_TRANSLATION_TIMEOUT_SECONDS = 180.0


class WordEditKind(StrEnum):
    """One deterministic word-level edit operation."""

    SUBSTITUTION = "substitution"
    DELETION = "deletion"
    INSERTION = "insertion"


class TranslationStructuralQuality(StrEnum):
    """Conservative structural categories, not semantic quality scores."""

    VALID = "valid"
    SOURCE_COPY = "source_copy"
    NON_TURKISH_SCRIPT = "non_turkish_script"
    MARKUP_LEAK = "markup_leak"
    SPECIAL_TOKEN_LEAK = "special_token_leak"
    EMPTY = "empty"
    UNEXPECTED_LANGUAGE = "unexpected_language"


class TranslationEvaluationOutcome(StrEnum):
    """The bounded outcome of one developer-only translation request."""

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


class TranslationProgressState(StrEnum):
    """One privacy-safe offline evaluator progress transition."""

    STARTED = "started"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityReferenceCase:
    """One explicitly supplied offline evaluation case."""

    identifier: str
    reference_de: str
    reference_tr: str | None = None
    audio_path: Path | None = None
    live_segments: tuple[str, ...] = ()
    evaluation_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep fixture content explicit and structurally safe."""

        if not self.identifier.strip():
            raise ValueError("Quality fixture ID must not be blank.")
        if not self.reference_de.strip():
            raise ValueError("Quality fixture German reference must not be blank.")
        if self.reference_tr is not None and not self.reference_tr.strip():
            raise ValueError("Quality fixture Turkish reference must not be blank.")
        if any(not segment.strip() for segment in self.live_segments):
            raise ValueError("Quality fixture live segments must not be blank.")
        if any(not term.strip() for term in self.evaluation_terms):
            raise ValueError("Quality fixture evaluation terms must not be blank.")


@dataclass(frozen=True, slots=True, kw_only=True)
class WordEdit:
    """One inspectable normalized-word mismatch."""

    kind: WordEditKind
    expected: str | None
    observed: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WordErrorRateReport:
    """A deterministic WER breakdown retaining raw and normalized text."""

    raw_reference: str
    raw_observed: str
    normalized_reference: str
    normalized_observed: str
    reference_word_count: int
    substitutions: int
    deletions: int
    insertions: int
    edits: tuple[WordEdit, ...]

    @property
    def wer(self) -> float:
        """Return standard WER, or zero for an empty reference/observation."""

        if self.reference_word_count == 0:
            return 0.0
        return (self.substitutions + self.deletions + self.insertions) / (
            self.reference_word_count
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class VocabularyObservation:
    """Show whether an explicitly supplied evaluation term survived STT."""

    term: str
    expected_present: bool
    observed_present: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationEvaluation:
    """One offline input/output pair and its structural classification."""

    source_text: str
    translated_text: str | None
    classification: TranslationStructuralQuality | None
    outcome: TranslationEvaluationOutcome
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationProgress:
    """Privacy-safe progress for one explicit evaluation phase and unit."""

    phase: str
    unit_index: int
    unit_count: int
    state: TranslationProgressState
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationBatchReport:
    """Classifications for one explicit group of translation requests."""

    entries: tuple[TranslationEvaluation, ...]

    def counts(self) -> dict[TranslationStructuralQuality, int]:
        """Return a deterministic count for every structural category."""

        counter = Counter(
            entry.classification
            for entry in self.entries
            if entry.classification is not None
        )
        return {
            category: counter[category] for category in TranslationStructuralQuality
        }

    @property
    def timeout_count(self) -> int:
        """Return the number of bounded provider/evaluator timeouts."""

        return sum(
            entry.outcome is TranslationEvaluationOutcome.TIMED_OUT
            for entry in self.entries
        )

    @property
    def failure_count(self) -> int:
        """Return non-timeout provider failures without raw provider details."""

        return sum(
            entry.outcome is TranslationEvaluationOutcome.FAILED
            for entry in self.entries
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityCaseReport:
    """Offline baseline evidence for one reference case."""

    case_id: str
    reference_tr: str | None
    stt: WordErrorRateReport | None
    vocabulary: tuple[VocabularyObservation, ...]
    clean_german_translation: TranslationBatchReport | None
    whole_passage_translation: TranslationBatchReport | None
    end_to_end_translation: TranslationBatchReport | None
    independent_fragment_translation: TranslationBatchReport | None
    sentence_context_translation: TranslationBatchReport | None


AudioChunkLoader = Callable[[Path], tuple[AudioInput, ...]]
TranslationProgressReporter = Callable[[TranslationProgress], None]


class OfflineQualityEvaluator:
    """Evaluate providers directly without entering the live runtime pipeline."""

    def __init__(
        self,
        *,
        speech_to_text_provider: SpeechToTextProvider | None,
        translation_provider: TranslationProvider | None,
        audio_loader: AudioChunkLoader | None = None,
        translation_timeout_seconds: float = (
            _DEFAULT_OFFLINE_TRANSLATION_TIMEOUT_SECONDS
        ),
        translation_progress_reporter: TranslationProgressReporter | None = None,
    ) -> None:
        """Store explicit offline dependencies; no provider is constructed here."""

        if (
            not math.isfinite(translation_timeout_seconds)
            or translation_timeout_seconds <= 0
        ):
            raise ValueError("Offline translation timeout must be finite and positive.")
        self._speech_to_text_provider = speech_to_text_provider
        self._translation_provider = translation_provider
        self._audio_loader = audio_loader
        self._translation_timeout_seconds = translation_timeout_seconds
        self._translation_progress_reporter = translation_progress_reporter

    async def evaluate(
        self,
        case: QualityReferenceCase,
        *,
        include_whole_passage: bool = False,
    ) -> QualityCaseReport:
        """Measure clean translation, STT, end-to-end, and segmentation separately."""

        observed_stt_text: str | None = None
        observed_stt_segments: tuple[str, ...] = ()
        stt_report: WordErrorRateReport | None = None
        if case.audio_path is not None and self._speech_to_text_provider is not None:
            if self._audio_loader is None:
                raise ValueError("STT evaluation requires an audio loader.")
            previous_accepted_text: str | None = None
            accepted_stt_segments: list[str] = []
            deduplicator = TranscriptDeduplicator()
            for audio in self._audio_loader(case.audio_path):
                transcription = await self._speech_to_text_provider.transcribe(
                    SpeechToTextRequest(
                        audio=audio,
                        language_hint=LanguageCode(value="de"),
                    )
                )
                for segment in transcription.segments:
                    accepted_text = deduplicator.deduplicate(
                        previous_text=previous_accepted_text,
                        current_text=segment.text,
                    )
                    if accepted_text is None:
                        continue
                    accepted_stt_segments.append(accepted_text)
                    previous_accepted_text = accepted_text
            observed_stt_segments = tuple(accepted_stt_segments)
            observed_stt_text = " ".join(observed_stt_segments)
            stt_report = calculate_word_error_rate(case.reference_de, observed_stt_text)

        vocabulary = tuple(
            VocabularyObservation(
                term=term,
                expected_present=_normalized_phrase_present(term, case.reference_de),
                observed_present=(
                    observed_stt_text is not None
                    and _normalized_phrase_present(term, observed_stt_text)
                ),
            )
            for term in case.evaluation_terms
        )

        clean_translation = await self._translate_units(
            phase="clean",
            units=split_german_context_units(case.reference_de),
            evaluation_terms=case.evaluation_terms,
        )
        fragment_units = case.live_segments or observed_stt_segments
        independent_translation = await self._translate_units(
            phase="fragment",
            units=fragment_units,
            evaluation_terms=case.evaluation_terms,
        )
        sentence_translation = await self._translate_units(
            phase="sentence_context",
            units=split_german_sentences(case.reference_de),
            evaluation_terms=case.evaluation_terms,
        )
        end_to_end_translation = await self._translate_units(
            phase="end_to_end",
            units=observed_stt_segments,
            evaluation_terms=case.evaluation_terms,
        )
        whole_passage_translation = (
            await self._translate_units(
                phase="whole_passage",
                units=(case.reference_de,),
                evaluation_terms=case.evaluation_terms,
            )
            if include_whole_passage
            else None
        )
        return QualityCaseReport(
            case_id=case.identifier,
            reference_tr=case.reference_tr,
            stt=stt_report,
            vocabulary=vocabulary,
            clean_german_translation=clean_translation,
            whole_passage_translation=whole_passage_translation,
            end_to_end_translation=end_to_end_translation,
            independent_fragment_translation=independent_translation,
            sentence_context_translation=sentence_translation,
        )

    async def _translate_units(
        self,
        *,
        phase: str,
        units: Iterable[str],
        evaluation_terms: tuple[str, ...],
    ) -> TranslationBatchReport | None:
        if self._translation_provider is None:
            return None
        non_blank_units = tuple(unit for unit in units if unit.strip())
        entries: list[TranslationEvaluation] = []
        for unit_index, unit in enumerate(non_blank_units, start=1):
            self._report_translation_progress(
                phase=phase,
                unit_index=unit_index,
                unit_count=len(non_blank_units),
                state=TranslationProgressState.STARTED,
            )
            started_at = time.monotonic()
            try:
                async with asyncio.timeout(self._translation_timeout_seconds):
                    result = await self._translation_provider.translate(
                        TranslationRequest(
                            text=unit,
                            source_language=LanguageCode(value="de"),
                            target_language=LanguageCode(value="tr"),
                        )
                    )
            except (ProviderTimeoutError, TimeoutError):
                elapsed_ms = _elapsed_milliseconds(started_at)
                entries.append(
                    TranslationEvaluation(
                        source_text=unit,
                        translated_text=None,
                        classification=None,
                        outcome=TranslationEvaluationOutcome.TIMED_OUT,
                        failure_reason="timeout",
                    )
                )
                self._report_translation_progress(
                    phase=phase,
                    unit_index=unit_index,
                    unit_count=len(non_blank_units),
                    state=TranslationProgressState.TIMED_OUT,
                    elapsed_ms=elapsed_ms,
                )
                continue
            except ProviderError:
                elapsed_ms = _elapsed_milliseconds(started_at)
                entries.append(
                    TranslationEvaluation(
                        source_text=unit,
                        translated_text=None,
                        classification=None,
                        outcome=TranslationEvaluationOutcome.FAILED,
                        failure_reason="provider_error",
                    )
                )
                self._report_translation_progress(
                    phase=phase,
                    unit_index=unit_index,
                    unit_count=len(non_blank_units),
                    state=TranslationProgressState.FAILED,
                    elapsed_ms=elapsed_ms,
                )
                continue
            elapsed_ms = _elapsed_milliseconds(started_at)
            entries.append(
                TranslationEvaluation(
                    source_text=unit,
                    translated_text=result.translated_text,
                    classification=classify_translation_output(
                        source_text=unit,
                        translated_text=result.translated_text,
                        allowed_unchanged_terms=evaluation_terms,
                    ),
                    outcome=TranslationEvaluationOutcome.COMPLETED,
                )
            )
            self._report_translation_progress(
                phase=phase,
                unit_index=unit_index,
                unit_count=len(non_blank_units),
                state=TranslationProgressState.COMPLETED,
                elapsed_ms=elapsed_ms,
            )
        return TranslationBatchReport(entries=tuple(entries))

    def _report_translation_progress(
        self,
        *,
        phase: str,
        unit_index: int,
        unit_count: int,
        state: TranslationProgressState,
        elapsed_ms: int | None = None,
    ) -> None:
        """Report fixed-label offline progress without any fixture text."""

        reporter = self._translation_progress_reporter
        if reporter is not None:
            reporter(
                TranslationProgress(
                    phase=phase,
                    unit_index=unit_index,
                    unit_count=unit_count,
                    state=state,
                    elapsed_ms=elapsed_ms,
                )
            )


def load_quality_reference_fixture(path: Path) -> tuple[QualityReferenceCase, ...]:
    """Parse an explicitly developer-supplied JSON fixture without reading audio."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Quality fixture could not be read.") from error
    if not isinstance(payload, dict) or not {"cases"} <= set(payload):
        raise ValueError("Quality fixture must contain a cases array.")
    if set(payload) - {"cases", "translation_benchmark"}:
        raise ValueError("Quality fixture contains unsupported top-level fields.")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Quality fixture cases must be a non-empty array.")
    return tuple(_parse_quality_case(raw_case, path.parent) for raw_case in raw_cases)


def normalize_german_for_wer(text: str) -> tuple[str, ...]:
    """Return deterministic Unicode-normalized words for offline WER only."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def calculate_word_error_rate(reference: str, observed: str) -> WordErrorRateReport:
    """Calculate deterministic word-level edit counts without changing raw text."""

    reference_words = normalize_german_for_wer(reference)
    observed_words = normalize_german_for_wer(observed)
    distances = _word_distances(reference_words, observed_words)
    edits = _trace_word_edits(reference_words, observed_words, distances)
    substitutions = sum(edit.kind is WordEditKind.SUBSTITUTION for edit in edits)
    deletions = sum(edit.kind is WordEditKind.DELETION for edit in edits)
    insertions = sum(edit.kind is WordEditKind.INSERTION for edit in edits)
    return WordErrorRateReport(
        raw_reference=reference,
        raw_observed=observed,
        normalized_reference=" ".join(reference_words),
        normalized_observed=" ".join(observed_words),
        reference_word_count=len(reference_words),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        edits=edits,
    )


def classify_translation_output(
    *,
    source_text: str,
    translated_text: str,
    allowed_unchanged_terms: Iterable[str] = (),
) -> TranslationStructuralQuality:
    """Classify only obvious structural translation defects conservatively."""

    stripped = translated_text.strip()
    if not stripped:
        return TranslationStructuralQuality.EMPTY
    if _SPECIAL_TOKEN_PATTERN.search(stripped):
        return TranslationStructuralQuality.SPECIAL_TOKEN_LEAK
    if _MARKUP_PATTERN.search(stripped):
        return TranslationStructuralQuality.MARKUP_LEAK
    if _has_unexpected_non_turkish_script(source_text, stripped):
        return TranslationStructuralQuality.NON_TURKISH_SCRIPT
    if _is_source_copy(source_text, stripped, allowed_unchanged_terms):
        return TranslationStructuralQuality.SOURCE_COPY
    if _looks_substantially_english(stripped, allowed_unchanged_terms):
        return TranslationStructuralQuality.UNEXPECTED_LANGUAGE
    return TranslationStructuralQuality.VALID


def split_german_sentences(text: str) -> tuple[str, ...]:
    """Split complete explicit reference text into simple sentence-sized units."""

    return tuple(
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
        if sentence.strip()
    )


def split_german_context_units(text: str) -> tuple[str, ...]:
    """Group complete German sentences into bounded coherent context units.

    The offline evaluator must not make a long private passage appear to be one
    live translation request.  This grouping is evaluation-only and does not
    change the production Assist segment boundary.
    """

    units: list[str] = []
    current_sentences: list[str] = []
    current_length = 0
    for sentence in split_german_sentences(text):
        separator_length = 1 if current_sentences else 0
        proposed_length = current_length + separator_length + len(sentence)
        if (
            current_sentences
            and proposed_length > _COHERENT_TRANSLATION_UNIT_MAX_CHARACTERS
        ):
            units.append(" ".join(current_sentences))
            current_sentences = [sentence]
            current_length = len(sentence)
            continue
        current_sentences.append(sentence)
        current_length = proposed_length
    if current_sentences:
        units.append(" ".join(current_sentences))
    return tuple(units)


def render_quality_report(report: QualityCaseReport) -> str:
    """Render only explicitly supplied offline evaluation content for a developer."""

    lines = [f"QUALITY BASELINE — {report.case_id}", "", "STT", "---"]
    if report.reference_tr is not None:
        lines.append(
            "A human Turkish reference was supplied for manual semantic comparison."
        )
    if report.stt is None:
        lines.append("Not evaluated (no audio input supplied).")
    else:
        stt = report.stt
        lines.extend(
            [
                f"Reference words: {stt.reference_word_count}",
                f"WER: {stt.wer:.3f}",
                f"Substitutions: {stt.substitutions}",
                f"Deletions: {stt.deletions}",
                f"Insertions: {stt.insertions}",
                f"Reference: {stt.raw_reference}",
                f"Observed: {stt.raw_observed}",
            ]
        )
        for edit in stt.edits:
            lines.append(
                f"{edit.kind.value}: expected={edit.expected!r} "
                f"observed={edit.observed!r}"
            )
    if report.vocabulary:
        lines.extend(["", "EVALUATION VOCABULARY", "---"])
        lines.extend(
            f"{item.term}: expected={item.expected_present} "
            f"observed={item.observed_present}"
            for item in report.vocabulary
        )
    lines.extend(
        _render_translation_batch(
            "TRANSLATION — CLEAN GERMAN CONTEXT", report.clean_german_translation
        )
    )
    if report.whole_passage_translation is not None:
        lines.extend(
            _render_translation_batch(
                "TRANSLATION — CLEAN GERMAN WHOLE PASSAGE",
                report.whole_passage_translation,
            )
        )
    lines.extend(_render_translation_batch("END TO END", report.end_to_end_translation))
    lines.extend(
        _render_translation_batch(
            "SEGMENTATION — INDEPENDENT FRAGMENTS",
            report.independent_fragment_translation,
        )
    )
    lines.extend(
        _render_translation_batch(
            "SEGMENTATION — SENTENCE CONTEXT",
            report.sentence_context_translation,
        )
    )
    return "\n".join(lines)


def _parse_quality_case(raw_case: object, base_directory: Path) -> QualityReferenceCase:
    if not isinstance(raw_case, dict):
        raise ValueError("Each quality fixture case must be an object.")
    allowed_fields = {
        "id",
        "reference_de",
        "reference_tr",
        "audio_path",
        "live_segments",
        "evaluation_terms",
    }
    if set(raw_case) - allowed_fields:
        raise ValueError("Quality fixture case contains unsupported fields.")
    identifier = _required_string(raw_case, "id")
    reference_de = _required_string(raw_case, "reference_de")
    reference_tr = _optional_string(raw_case, "reference_tr")
    audio_path_value = _optional_string(raw_case, "audio_path")
    return QualityReferenceCase(
        identifier=identifier,
        reference_de=reference_de,
        reference_tr=reference_tr,
        audio_path=(base_directory / audio_path_value) if audio_path_value else None,
        live_segments=_string_tuple(raw_case, "live_segments"),
        evaluation_terms=_string_tuple(raw_case, "evaluation_terms"),
    )


def _required_string(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Quality fixture {field_name} must be a non-blank string.")
    return value


def _optional_string(payload: dict[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Quality fixture {field_name} must be a non-blank string.")
    return value


def _string_tuple(payload: dict[str, object], field_name: str) -> tuple[str, ...]:
    value = payload.get(field_name, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"Quality fixture {field_name} must be an array of strings.")
    return tuple(value)


def _word_distances(
    reference: tuple[str, ...], observed: tuple[str, ...]
) -> list[list[int]]:
    distances = [[0] * (len(observed) + 1) for _ in range(len(reference) + 1)]
    for reference_index in range(1, len(reference) + 1):
        distances[reference_index][0] = reference_index
    for observed_index in range(1, len(observed) + 1):
        distances[0][observed_index] = observed_index
    for reference_index, reference_word in enumerate(reference, start=1):
        for observed_index, observed_word in enumerate(observed, start=1):
            substitution_cost = 0 if reference_word == observed_word else 1
            distances[reference_index][observed_index] = min(
                distances[reference_index - 1][observed_index - 1] + substitution_cost,
                distances[reference_index - 1][observed_index] + 1,
                distances[reference_index][observed_index - 1] + 1,
            )
    return distances


def _trace_word_edits(
    reference: tuple[str, ...],
    observed: tuple[str, ...],
    distances: list[list[int]],
) -> tuple[WordEdit, ...]:
    edits: list[WordEdit] = []
    reference_index = len(reference)
    observed_index = len(observed)
    while reference_index > 0 or observed_index > 0:
        if (
            reference_index > 0
            and observed_index > 0
            and reference[reference_index - 1] == observed[observed_index - 1]
            and distances[reference_index][observed_index]
            == distances[reference_index - 1][observed_index - 1]
        ):
            reference_index -= 1
            observed_index -= 1
            continue
        if (
            reference_index > 0
            and observed_index > 0
            and distances[reference_index][observed_index]
            == distances[reference_index - 1][observed_index - 1] + 1
        ):
            edits.append(
                WordEdit(
                    kind=WordEditKind.SUBSTITUTION,
                    expected=reference[reference_index - 1],
                    observed=observed[observed_index - 1],
                )
            )
            reference_index -= 1
            observed_index -= 1
            continue
        if (
            reference_index > 0
            and distances[reference_index][observed_index]
            == distances[reference_index - 1][observed_index] + 1
        ):
            edits.append(
                WordEdit(
                    kind=WordEditKind.DELETION,
                    expected=reference[reference_index - 1],
                    observed=None,
                )
            )
            reference_index -= 1
            continue
        edits.append(
            WordEdit(
                kind=WordEditKind.INSERTION,
                expected=None,
                observed=observed[observed_index - 1],
            )
        )
        observed_index -= 1
    edits.reverse()
    return tuple(edits)


def _normalized_phrase_present(phrase: str, text: str) -> bool:
    normalized_phrase = normalize_german_for_wer(phrase)
    normalized_text = normalize_german_for_wer(text)
    phrase_size = len(normalized_phrase)
    return phrase_size > 0 and any(
        normalized_text[index : index + phrase_size] == normalized_phrase
        for index in range(len(normalized_text) - phrase_size + 1)
    )


def _is_source_copy(
    source_text: str,
    translated_text: str,
    allowed_unchanged_terms: Iterable[str],
) -> bool:
    source_words = normalize_german_for_wer(source_text)
    translated_words = normalize_german_for_wer(translated_text)
    if len(source_words) < 2 or source_words != translated_words:
        return False
    return not any(
        source_words == normalize_german_for_wer(term)
        for term in allowed_unchanged_terms
    )


def _has_unexpected_non_turkish_script(source_text: str, translated_text: str) -> bool:
    """Flag a foreign-script artifact unless that exact character was in source.

    A German source can legitimately contain a name written in another script,
    which a Turkish translation may preserve. Any newly generated CJK, Hangul,
    Katakana, or Cyrillic character is instead an objective offline anomaly.
    """

    source_characters = set(source_text)
    return any(
        character not in source_characters and _is_cjk_or_hangul_or_cyrillic(character)
        for character in translated_text
    )


def _is_cjk_or_hangul_or_cyrillic(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x0400 <= codepoint <= 0x052F
        or 0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _looks_substantially_english(
    text: str,
    allowed_unchanged_terms: Iterable[str],
) -> bool:
    allowed_words = {
        word
        for term in allowed_unchanged_terms
        for word in normalize_german_for_wer(term)
    }
    words = [
        word for word in normalize_german_for_wer(text) if word not in allowed_words
    ]
    english_function_words = {
        "a",
        "an",
        "and",
        "are",
        "at",
        "back",
        "be",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "the",
        "to",
        "we",
        "with",
        "you",
    }
    turkish_markers = {
        "bir",
        "bu",
        "da",
        "de",
        "gibi",
        "için",
        "ile",
        "mi",
        "m\u0131",
        "mu",
        "mü",
        "ve",
        "ya",
    }
    return (
        len(words) >= 3
        and sum(word in english_function_words for word in words) >= 3
        and not any(word in turkish_markers for word in words)
    )


def _render_translation_batch(
    heading: str,
    report: TranslationBatchReport | None,
) -> list[str]:
    lines = ["", heading, "---"]
    if report is None:
        lines.append("Not evaluated (no translation provider supplied).")
        return lines
    lines.append(f"Segments evaluated: {len(report.entries)}")
    lines.append(
        "Completed: "
        + str(
            sum(
                entry.outcome is TranslationEvaluationOutcome.COMPLETED
                for entry in report.entries
            )
        )
    )
    lines.append(f"Timeouts: {report.timeout_count}")
    lines.append(f"Provider failures: {report.failure_count}")
    for category, count in report.counts().items():
        lines.append(f"{category.value}: {count}")
    for entry in report.entries:
        if entry.outcome is not TranslationEvaluationOutcome.COMPLETED:
            lines.extend(
                [
                    f"Source: {entry.source_text}",
                    f"Outcome: {entry.outcome.value}",
                    f"Failure: {entry.failure_reason}",
                ]
            )
            continue
        lines.extend(
            [
                f"Source: {entry.source_text}",
                f"Translation: {entry.translated_text}",
                "Classification: "
                + (
                    entry.classification.value
                    if entry.classification is not None
                    else "not_classified"
                ),
            ]
        )
    return lines


_SPECIAL_TOKEN_PATTERN = re.compile(r"<\s*(?:bos|eos|pad|s|/s|unk)\s*>", re.IGNORECASE)
_MARKUP_PATTERN = re.compile(r"</?[A-Za-z][^>]{0,120}>")


def _elapsed_milliseconds(started_at: float) -> int:
    """Return a privacy-safe elapsed duration for developer progress only."""

    return int((time.monotonic() - started_at) * 1_000)
