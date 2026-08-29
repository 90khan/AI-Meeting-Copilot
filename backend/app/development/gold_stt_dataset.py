"""Private, offline-only timestamp-aligned German STT gold evaluation.

Gold reference records and model predictions have deliberately separate JSONL
schemas.  This module is never imported by the live STT, Assist, or telemetry
paths; callers must supply private files explicitly.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:-[^\W_]+)*", flags=re.UNICODE)
_GERMAN_NUMBER_WORDS = frozenset(
    {
        "null",
        "eins",
        "zwei",
        "drei",
        "vier",
        "fünf",
        "sechs",
        "sieben",
        "acht",
        "neun",
        "zehn",
        "elf",
        "zwölf",
        "dreizehn",
        "vierzehn",
        "fünfzehn",
        "sechzehn",
        "siebzehn",
        "achtzehn",
        "neunzehn",
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


class GoldReferenceState(StrEnum):
    """Human-review state for a private gold interval."""

    VERIFIED = "verified"
    UNCERTAIN = "uncertain"
    UNINTELLIGIBLE = "unintelligible"
    PARTIAL_WORD = "partial_word"
    INTERRUPTED = "interrupted"


class GoldReviewStatus(StrEnum):
    """The bounded review-status metadata stored with a gold interval."""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    VERIFIED = "verified"


class GoldTermCategory(StrEnum):
    """Supported explicit terminology annotations."""

    TECHNICAL_TERM = "technical_term"
    PERSON_NAME = "person_name"
    ORGANIZATION = "organization"
    LOCATION = "location"
    ENGLISH_TERM = "english_term"


class GoldNumberKind(StrEnum):
    """The semantic class of one explicitly annotated number."""

    CARDINAL = "cardinal"
    ORDINAL = "ordinal"
    DURATION = "duration"
    DATE = "date"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    OTHER = "other"


class NumberEvaluationOutcome(StrEnum):
    """One evaluated numeric occurrence, never inferred from technical IDs."""

    SURFACE_MATCH = "surface_match"
    VALUE_MATCH = "value_match"
    SUBSTITUTION = "substitution"
    DELETION = "deletion"


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldReviewMetadata:
    """Bounded review and acoustic-condition metadata without reviewer notes."""

    review_status: GoldReviewStatus
    reviewer_count: int
    has_overlap: bool = False
    has_noise: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.review_status, GoldReviewStatus):
            raise ValueError("Gold review status is invalid.")
        if (
            not isinstance(self.reviewer_count, int)
            or isinstance(self.reviewer_count, bool)
            or self.reviewer_count < 0
        ):
            raise ValueError("Gold reviewer count must be non-negative.")
        if not isinstance(self.has_overlap, bool) or not isinstance(
            self.has_noise, bool
        ):
            raise ValueError("Gold acoustic annotations must be boolean.")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldTechnicalTerm:
    """One explicitly annotated term occurrence in a gold segment."""

    surface: str
    canonical: str
    category: GoldTermCategory

    def __post_init__(self) -> None:
        _require_non_blank(self.surface, "Gold term surface")
        _require_non_blank(self.canonical, "Gold term canonical value")
        if not isinstance(self.category, GoldTermCategory):
            raise ValueError("Gold term category is invalid.")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldNumber:
    """One explicit number occurrence; equivalences are annotation supplied."""

    surface: str
    canonical_value: str
    kind: GoldNumberKind
    unit: str | None = None
    value_forms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_blank(self.surface, "Gold number surface")
        _require_non_blank(self.canonical_value, "Gold number canonical value")
        if not isinstance(self.kind, GoldNumberKind):
            raise ValueError("Gold number kind is invalid.")
        if self.unit is not None:
            _require_non_blank(self.unit, "Gold number unit")
        if any(
            not isinstance(item, str) or not item.strip() for item in self.value_forms
        ):
            raise ValueError("Gold number value forms must be non-blank strings.")
        if len(set(self.value_forms)) != len(self.value_forms):
            raise ValueError("Gold number value forms must be unique.")

    @property
    def normalized_value_forms(self) -> tuple[tuple[str, ...], ...]:
        """Return annotation-defined equivalent forms without numeric coercion."""

        forms = (self.surface, self.canonical_value, *self.value_forms)
        return tuple(
            dict.fromkeys(
                normalized
                for form in forms
                if (normalized := normalize_german_tokens(form))
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldSttSegment:
    """One private timestamp-aligned, human-reviewed German audio interval."""

    schema_version: int
    audio_id: str
    segment_id: str
    start_ms: int
    end_ms: int
    speaker: str | None
    text_de: str
    normalized_text_de: str | None
    reference_state: GoldReferenceState
    evaluation_eligible: bool
    tags: tuple[str, ...]
    terms: tuple[GoldTechnicalTerm, ...]
    numbers: tuple[GoldNumber, ...]
    annotations: GoldReviewMetadata

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("Gold schema version is unsupported.")
        _require_identifier(self.audio_id, "Gold audio ID")
        _require_identifier(self.segment_id, "Gold segment ID")
        _require_timestamp_interval(self.start_ms, self.end_ms)
        if self.speaker is not None:
            _require_non_blank(self.speaker, "Gold speaker")
        if not isinstance(self.reference_state, GoldReferenceState):
            raise ValueError("Gold reference state is invalid.")
        if not isinstance(self.evaluation_eligible, bool):
            raise ValueError("Gold evaluation eligibility must be boolean.")
        if (
            self.evaluation_eligible
            and self.reference_state is not GoldReferenceState.VERIFIED
        ):
            raise ValueError("Only verified gold intervals may be evaluation eligible.")
        if self.reference_state is GoldReferenceState.VERIFIED:
            _require_non_blank(self.text_de, "Verified gold text")
            if self.annotations.review_status is not GoldReviewStatus.VERIFIED:
                raise ValueError(
                    "Verified gold text requires verified review metadata."
                )
            if self.annotations.reviewer_count < 1:
                raise ValueError("Verified gold text requires a human reviewer.")
        elif self.evaluation_eligible:
            raise ValueError("Non-verified gold text must remain excluded.")
        if self.text_de and not isinstance(self.text_de, str):
            raise ValueError("Gold text must be a string.")
        derived = self.derived_normalized_text_de
        if self.normalized_text_de is not None:
            _require_non_blank(self.normalized_text_de, "Gold normalized text")
            if self.normalized_text_de != derived:
                raise ValueError(
                    "Gold normalized text must match deterministic normalization."
                )
        if any(not isinstance(tag, str) or not _is_valid_tag(tag) for tag in self.tags):
            raise ValueError("Gold tags must be non-blank snake-case labels.")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("Gold tags must be unique.")
        for term in self.terms:
            if not _normalized_phrase_present(term.surface, self.text_de):
                raise ValueError("Gold term surface must occur in gold text.")
        for number in self.numbers:
            if not _normalized_phrase_present(number.surface, self.text_de):
                raise ValueError("Gold number surface must occur in gold text.")

    @property
    def duration_ms(self) -> int:
        """Return this half-open interval's exact duration."""

        return self.end_ms - self.start_ms

    @property
    def derived_normalized_text_de(self) -> str:
        """Return the deterministic, model-independent comparison text."""

        return normalize_german_text(self.text_de)


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldSttDataset:
    """A single-audio private gold dataset in deterministic interval order."""

    audio_id: str
    segments: tuple[GoldSttSegment, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.audio_id, "Gold dataset audio ID")
        if not self.segments:
            raise ValueError("Gold dataset must contain at least one segment.")
        segment_ids: set[str] = set()
        previous_end_ms = -1
        for segment in self.segments:
            if segment.audio_id != self.audio_id:
                raise ValueError("Gold dataset segments must share one audio ID.")
            if segment.segment_id in segment_ids:
                raise ValueError("Gold dataset segment IDs must be unique.")
            if segment.start_ms < previous_end_ms:
                raise ValueError(
                    "Gold dataset intervals must be ordered and non-overlapping."
                )
            segment_ids.add(segment.segment_id)
            previous_end_ms = segment.end_ms

    @property
    def eligible_segments(self) -> tuple[GoldSttSegment, ...]:
        """Return only human-verified intervals included in primary metrics."""

        return tuple(
            segment for segment in self.segments if segment.evaluation_eligible
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SttPrediction:
    """A model hypothesis record that cannot contain gold-reference fields."""

    schema_version: int
    audio_id: str
    segment_id: str
    run_id: str
    model_label: str
    hypothesis_de: str

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("Prediction schema version is unsupported.")
        _require_identifier(self.audio_id, "Prediction audio ID")
        _require_identifier(self.segment_id, "Prediction segment ID")
        _require_identifier(self.run_id, "Prediction run ID")
        _require_non_blank(self.model_label, "Prediction model label")
        if not isinstance(self.hypothesis_de, str):
            raise ValueError("Prediction hypothesis must be a string.")


@dataclass(frozen=True, slots=True, kw_only=True)
class SttPredictionDataset:
    """One coherent local prediction run for a single private audio source."""

    audio_id: str
    run_id: str
    model_label: str
    predictions: tuple[SttPrediction, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.audio_id, "Prediction dataset audio ID")
        _require_identifier(self.run_id, "Prediction dataset run ID")
        _require_non_blank(self.model_label, "Prediction dataset model label")
        prediction_ids: set[str] = set()
        for prediction in self.predictions:
            if (
                prediction.audio_id != self.audio_id
                or prediction.run_id != self.run_id
                or prediction.model_label != self.model_label
            ):
                raise ValueError("Prediction records must share dataset metadata.")
            if prediction.segment_id in prediction_ids:
                raise ValueError("Prediction segment IDs must be unique.")
            prediction_ids.add(prediction.segment_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class TextErrorCounts:
    """Levenshtein counts for either word or character evaluation."""

    reference_count: int
    hypothesis_count: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def error_rate(self) -> float:
        """Return error count per reference item, or zero for an empty reference."""

        if self.reference_count == 0:
            return 0.0
        return (
            self.substitutions + self.deletions + self.insertions
        ) / self.reference_count


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldSegmentMetrics:
    """Gold-aligned WER/CER and audio duration for one verified interval."""

    segment_id: str
    duration_ms: int
    word_errors: TextErrorCounts
    character_errors: TextErrorCounts


@dataclass(frozen=True, slots=True, kw_only=True)
class TermOccurrenceMetrics:
    """One explicitly annotated term occurrence and a bounded confusion hint."""

    segment_id: str
    canonical: str
    exact_match: bool
    normalized_match: bool
    observed_confusion: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TermConfusion:
    """A deterministic unmatched-term observation for private inspection."""

    canonical: str
    observed: str | None
    occurrence_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TechnicalTermMetrics:
    """Aggregate metrics for only explicitly annotated term occurrences."""

    occurrence_count: int
    exact_match_count: int
    normalized_match_count: int
    recall: float
    occurrences: tuple[TermOccurrenceMetrics, ...]
    confusions: tuple[TermConfusion, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class NumberOccurrenceMetrics:
    """One explicit number annotation and its surface/value result."""

    segment_id: str
    canonical_value: str
    surface_match: bool
    value_match: bool
    outcome: NumberEvaluationOutcome


@dataclass(frozen=True, slots=True, kw_only=True)
class NumberMetrics:
    """Separate number metrics; technical IDs are never inferred as numbers."""

    occurrence_count: int
    surface_match_count: int
    value_match_count: int
    substitutions: int
    deletions: int
    insertions: int
    occurrences: tuple[NumberOccurrenceMetrics, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldAlignedSttReport:
    """Primary accuracy report for verified gold intervals only."""

    audio_id: str
    evaluated_audio_duration_ms: int
    scored_segment_count: int
    word_errors: TextErrorCounts
    character_errors: TextErrorCounts
    per_segment: tuple[GoldSegmentMetrics, ...]
    technical_terms: TechnicalTermMetrics
    numbers: NumberMetrics


def normalize_german_tokens(text: str) -> tuple[str, ...]:
    """Return model-independent NFC, casefolded German comparison tokens.

    Internal hyphens remain part of one token, so identifiers such as
    ``Re-Ranking`` and ordinary hyphenated compounds are not split.  Numbers
    remain lexical tokens; no word-to-digit or model-confusion rewrites occur.
    """

    if not isinstance(text, str):
        raise ValueError("German evaluation text must be a string.")
    normalized = unicodedata.normalize("NFC", text).casefold()
    return tuple(_TOKEN_PATTERN.findall(normalized))


def normalize_german_text(text: str) -> str:
    """Return deterministic normalized text with one space between tokens."""

    return " ".join(normalize_german_tokens(text))


def load_gold_stt_dataset(path: Path) -> GoldSttDataset:
    """Load strict private gold JSONL without accepting path or prediction fields."""

    records = _load_jsonl_records(path, "Gold dataset")
    segments = tuple(_parse_gold_segment(record) for record in records)
    return GoldSttDataset(audio_id=segments[0].audio_id, segments=segments)


def load_stt_prediction_dataset(path: Path) -> SttPredictionDataset:
    """Load strict private prediction JSONL without accepting gold-text fields."""

    records = _load_jsonl_records(path, "Prediction dataset")
    predictions = tuple(_parse_prediction(record) for record in records)
    return SttPredictionDataset(
        audio_id=predictions[0].audio_id,
        run_id=predictions[0].run_id,
        model_label=predictions[0].model_label,
        predictions=predictions,
    )


def evaluate_gold_aligned_stt(
    *,
    gold: GoldSttDataset,
    predictions: SttPredictionDataset,
) -> GoldAlignedSttReport:
    """Evaluate predictions only against explicitly eligible verified gold text."""

    if gold.audio_id != predictions.audio_id:
        raise ValueError("Gold and prediction audio IDs must match.")
    predictions_by_segment = {
        prediction.segment_id: prediction for prediction in predictions.predictions
    }
    gold_ids = {segment.segment_id for segment in gold.segments}
    if set(predictions_by_segment) - gold_ids:
        raise ValueError("Predictions contain unknown gold segment IDs.")
    eligible = gold.eligible_segments
    if not eligible:
        raise ValueError("Gold dataset has no eligible verified segments.")
    missing_ids = [
        segment.segment_id
        for segment in eligible
        if segment.segment_id not in predictions_by_segment
    ]
    if missing_ids:
        raise ValueError("Predictions are missing eligible gold segments.")

    per_segment: list[GoldSegmentMetrics] = []
    term_occurrences: list[TermOccurrenceMetrics] = []
    number_occurrences: list[NumberOccurrenceMetrics] = []
    number_insertions = 0
    for segment in eligible:
        prediction = predictions_by_segment[segment.segment_id]
        reference_tokens = normalize_german_tokens(segment.text_de)
        hypothesis_tokens = normalize_german_tokens(prediction.hypothesis_de)
        per_segment.append(
            GoldSegmentMetrics(
                segment_id=segment.segment_id,
                duration_ms=segment.duration_ms,
                word_errors=_calculate_error_counts(
                    reference_tokens, hypothesis_tokens
                ),
                character_errors=_calculate_error_counts(
                    tuple("".join(reference_tokens)),
                    tuple("".join(hypothesis_tokens)),
                ),
            )
        )
        term_occurrences.extend(
            _evaluate_terms(segment=segment, hypothesis_text=prediction.hypothesis_de)
        )
        segment_numbers, insertions = _evaluate_numbers(
            segment=segment,
            hypothesis_text=prediction.hypothesis_de,
        )
        number_occurrences.extend(segment_numbers)
        number_insertions += insertions

    return GoldAlignedSttReport(
        audio_id=gold.audio_id,
        evaluated_audio_duration_ms=sum(item.duration_ms for item in per_segment),
        scored_segment_count=len(per_segment),
        word_errors=_aggregate_error_counts(item.word_errors for item in per_segment),
        character_errors=_aggregate_error_counts(
            item.character_errors for item in per_segment
        ),
        per_segment=tuple(per_segment),
        technical_terms=_aggregate_term_metrics(term_occurrences),
        numbers=_aggregate_number_metrics(number_occurrences, number_insertions),
    )


def _evaluate_terms(
    *,
    segment: GoldSttSegment,
    hypothesis_text: str,
) -> tuple[TermOccurrenceMetrics, ...]:
    hypothesis_tokens = normalize_german_tokens(hypothesis_text)
    occurrences: list[TermOccurrenceMetrics] = []
    for term in segment.terms:
        expected_tokens = normalize_german_tokens(term.surface)
        exact_match = _raw_phrase_present(term.surface, hypothesis_text)
        normalized_match = _phrase_tokens_present(expected_tokens, hypothesis_tokens)
        occurrences.append(
            TermOccurrenceMetrics(
                segment_id=segment.segment_id,
                canonical=term.canonical,
                exact_match=exact_match,
                normalized_match=normalized_match,
                observed_confusion=(
                    None
                    if normalized_match
                    else _closest_token_window(expected_tokens, hypothesis_tokens)
                ),
            )
        )
    return tuple(occurrences)


def _evaluate_numbers(
    *,
    segment: GoldSttSegment,
    hypothesis_text: str,
) -> tuple[tuple[NumberOccurrenceMetrics, ...], int]:
    hypothesis_tokens = normalize_german_tokens(hypothesis_text)
    consumed_token_indices: set[int] = set()
    occurrences: list[NumberOccurrenceMetrics] = []
    for number in segment.numbers:
        surface_match = _raw_phrase_present(number.surface, hypothesis_text)
        matched_span = _find_first_phrase_span(
            hypothesis_tokens,
            normalize_german_tokens(number.surface),
            excluded_indices=consumed_token_indices,
        )
        value_span = matched_span
        if value_span is None:
            for form in number.normalized_value_forms:
                value_span = _find_first_phrase_span(
                    hypothesis_tokens,
                    form,
                    excluded_indices=consumed_token_indices,
                )
                if value_span is not None:
                    break
        if value_span is not None:
            consumed_token_indices.update(range(value_span[0], value_span[1]))
            outcome = (
                NumberEvaluationOutcome.SURFACE_MATCH
                if surface_match
                else NumberEvaluationOutcome.VALUE_MATCH
            )
            occurrences.append(
                NumberOccurrenceMetrics(
                    segment_id=segment.segment_id,
                    canonical_value=number.canonical_value,
                    surface_match=surface_match,
                    value_match=True,
                    outcome=outcome,
                )
            )
            continue
        has_unmatched_number = any(
            index not in consumed_token_indices and _is_number_token(token)
            for index, token in enumerate(hypothesis_tokens)
        )
        occurrences.append(
            NumberOccurrenceMetrics(
                segment_id=segment.segment_id,
                canonical_value=number.canonical_value,
                surface_match=False,
                value_match=False,
                outcome=(
                    NumberEvaluationOutcome.SUBSTITUTION
                    if has_unmatched_number
                    else NumberEvaluationOutcome.DELETION
                ),
            )
        )
    insertions = sum(
        index not in consumed_token_indices and _is_number_token(token)
        for index, token in enumerate(hypothesis_tokens)
    )
    return tuple(occurrences), insertions


def _aggregate_term_metrics(
    occurrences: Iterable[TermOccurrenceMetrics],
) -> TechnicalTermMetrics:
    resolved = tuple(occurrences)
    confusion_counter = Counter(
        (item.canonical, item.observed_confusion)
        for item in resolved
        if not item.normalized_match
    )
    return TechnicalTermMetrics(
        occurrence_count=len(resolved),
        exact_match_count=sum(item.exact_match for item in resolved),
        normalized_match_count=sum(item.normalized_match for item in resolved),
        recall=(
            sum(item.normalized_match for item in resolved) / len(resolved)
            if resolved
            else 0.0
        ),
        occurrences=resolved,
        confusions=tuple(
            TermConfusion(
                canonical=canonical,
                observed=observed,
                occurrence_count=count,
            )
            for (canonical, observed), count in sorted(confusion_counter.items())
        ),
    )


def _aggregate_number_metrics(
    occurrences: Iterable[NumberOccurrenceMetrics],
    insertions: int,
) -> NumberMetrics:
    resolved = tuple(occurrences)
    return NumberMetrics(
        occurrence_count=len(resolved),
        surface_match_count=sum(item.surface_match for item in resolved),
        value_match_count=sum(item.value_match for item in resolved),
        substitutions=sum(
            item.outcome is NumberEvaluationOutcome.SUBSTITUTION for item in resolved
        ),
        deletions=sum(
            item.outcome is NumberEvaluationOutcome.DELETION for item in resolved
        ),
        insertions=insertions,
        occurrences=resolved,
    )


def _calculate_error_counts(
    reference: Sequence[str],
    hypothesis: Sequence[str],
) -> TextErrorCounts:
    distances = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for reference_index in range(1, len(reference) + 1):
        distances[reference_index][0] = reference_index
    for hypothesis_index in range(1, len(hypothesis) + 1):
        distances[0][hypothesis_index] = hypothesis_index
    for reference_index, expected in enumerate(reference, start=1):
        for hypothesis_index, observed in enumerate(hypothesis, start=1):
            substitution_cost = 0 if expected == observed else 1
            distances[reference_index][hypothesis_index] = min(
                distances[reference_index - 1][hypothesis_index] + 1,
                distances[reference_index][hypothesis_index - 1] + 1,
                distances[reference_index - 1][hypothesis_index - 1]
                + substitution_cost,
            )
    substitutions = deletions = insertions = 0
    reference_index = len(reference)
    hypothesis_index = len(hypothesis)
    while reference_index or hypothesis_index:
        if (
            reference_index
            and hypothesis_index
            and reference[reference_index - 1] == hypothesis[hypothesis_index - 1]
            and distances[reference_index][hypothesis_index]
            == distances[reference_index - 1][hypothesis_index - 1]
        ):
            reference_index -= 1
            hypothesis_index -= 1
        elif (
            reference_index
            and hypothesis_index
            and distances[reference_index][hypothesis_index]
            == distances[reference_index - 1][hypothesis_index - 1] + 1
        ):
            substitutions += 1
            reference_index -= 1
            hypothesis_index -= 1
        elif (
            reference_index
            and distances[reference_index][hypothesis_index]
            == distances[reference_index - 1][hypothesis_index] + 1
        ):
            deletions += 1
            reference_index -= 1
        else:
            insertions += 1
            hypothesis_index -= 1
    return TextErrorCounts(
        reference_count=len(reference),
        hypothesis_count=len(hypothesis),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
    )


def _aggregate_error_counts(items: Iterable[TextErrorCounts]) -> TextErrorCounts:
    resolved = tuple(items)
    return TextErrorCounts(
        reference_count=sum(item.reference_count for item in resolved),
        hypothesis_count=sum(item.hypothesis_count for item in resolved),
        substitutions=sum(item.substitutions for item in resolved),
        deletions=sum(item.deletions for item in resolved),
        insertions=sum(item.insertions for item in resolved),
    )


def _load_jsonl_records(path: Path, label: str) -> tuple[Mapping[str, Any], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"{label} could not be read.") from error
    if not lines:
        raise ValueError(f"{label} must contain at least one JSONL record.")
    records: list[Mapping[str, Any]] = []
    for line in lines:
        if not line.strip():
            raise ValueError(f"{label} must not contain blank JSONL records.")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} contains invalid JSONL.") from error
        if not isinstance(record, dict):
            raise ValueError(f"{label} JSONL records must be objects.")
        records.append(record)
    return tuple(records)


def _parse_gold_segment(record: Mapping[str, Any]) -> GoldSttSegment:
    allowed = {
        "schema_version",
        "audio_id",
        "segment_id",
        "start_ms",
        "end_ms",
        "speaker",
        "text_de",
        "normalized_text_de",
        "reference_state",
        "evaluation_eligible",
        "tags",
        "terms",
        "numbers",
        "annotations",
    }
    _require_exact_keys(record, allowed, "Gold record")
    return GoldSttSegment(
        schema_version=_require_int(record, "schema_version", "Gold record"),
        audio_id=_require_string(record, "audio_id", "Gold record"),
        segment_id=_require_string(record, "segment_id", "Gold record"),
        start_ms=_require_int(record, "start_ms", "Gold record"),
        end_ms=_require_int(record, "end_ms", "Gold record"),
        speaker=_optional_string(record, "speaker", "Gold record"),
        text_de=_require_string(record, "text_de", "Gold record", allow_blank=True),
        normalized_text_de=_optional_string(
            record, "normalized_text_de", "Gold record"
        ),
        reference_state=_parse_enum(
            GoldReferenceState,
            _require_string(record, "reference_state", "Gold record"),
            "Gold reference state",
        ),
        evaluation_eligible=_require_bool(record, "evaluation_eligible", "Gold record"),
        tags=_parse_tags(record),
        terms=_parse_terms(record),
        numbers=_parse_numbers(record),
        annotations=_parse_annotations(record),
    )


def _parse_prediction(record: Mapping[str, Any]) -> SttPrediction:
    allowed = {
        "schema_version",
        "audio_id",
        "segment_id",
        "run_id",
        "model_label",
        "hypothesis_de",
    }
    _require_exact_keys(record, allowed, "Prediction record")
    return SttPrediction(
        schema_version=_require_int(record, "schema_version", "Prediction record"),
        audio_id=_require_string(record, "audio_id", "Prediction record"),
        segment_id=_require_string(record, "segment_id", "Prediction record"),
        run_id=_require_string(record, "run_id", "Prediction record"),
        model_label=_require_string(record, "model_label", "Prediction record"),
        hypothesis_de=_require_string(
            record, "hypothesis_de", "Prediction record", allow_blank=True
        ),
    )


def _parse_tags(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw_tags = record["tags"]
    if not isinstance(raw_tags, list) or any(
        not isinstance(tag, str) for tag in raw_tags
    ):
        raise ValueError("Gold tags must be an array of strings.")
    return tuple(raw_tags)


def _parse_terms(record: Mapping[str, Any]) -> tuple[GoldTechnicalTerm, ...]:
    raw_terms = record["terms"]
    if not isinstance(raw_terms, list):
        raise ValueError("Gold terms must be an array.")
    terms: list[GoldTechnicalTerm] = []
    for raw_term in raw_terms:
        if not isinstance(raw_term, dict):
            raise ValueError("Gold terms must be objects.")
        _require_exact_keys(raw_term, {"surface", "canonical", "category"}, "Gold term")
        terms.append(
            GoldTechnicalTerm(
                surface=_require_string(raw_term, "surface", "Gold term"),
                canonical=_require_string(raw_term, "canonical", "Gold term"),
                category=_parse_enum(
                    GoldTermCategory,
                    _require_string(raw_term, "category", "Gold term"),
                    "Gold term category",
                ),
            )
        )
    return tuple(terms)


def _parse_numbers(record: Mapping[str, Any]) -> tuple[GoldNumber, ...]:
    raw_numbers = record["numbers"]
    if not isinstance(raw_numbers, list):
        raise ValueError("Gold numbers must be an array.")
    numbers: list[GoldNumber] = []
    for raw_number in raw_numbers:
        if not isinstance(raw_number, dict):
            raise ValueError("Gold numbers must be objects.")
        _require_exact_keys(
            raw_number,
            {"surface", "canonical_value", "kind", "unit", "value_forms"},
            "Gold number",
        )
        raw_forms = raw_number["value_forms"]
        if not isinstance(raw_forms, list) or any(
            not isinstance(form, str) for form in raw_forms
        ):
            raise ValueError("Gold number value forms must be an array of strings.")
        numbers.append(
            GoldNumber(
                surface=_require_string(raw_number, "surface", "Gold number"),
                canonical_value=_require_string(
                    raw_number, "canonical_value", "Gold number"
                ),
                kind=_parse_enum(
                    GoldNumberKind,
                    _require_string(raw_number, "kind", "Gold number"),
                    "Gold number kind",
                ),
                unit=_optional_string(raw_number, "unit", "Gold number"),
                value_forms=tuple(raw_forms),
            )
        )
    return tuple(numbers)


def _parse_annotations(record: Mapping[str, Any]) -> GoldReviewMetadata:
    raw = record["annotations"]
    if not isinstance(raw, dict):
        raise ValueError("Gold annotations must be an object.")
    _require_exact_keys(
        raw,
        {"review_status", "reviewer_count", "has_overlap", "has_noise"},
        "Gold annotations",
    )
    return GoldReviewMetadata(
        review_status=_parse_enum(
            GoldReviewStatus,
            _require_string(raw, "review_status", "Gold annotations"),
            "Gold review status",
        ),
        reviewer_count=_require_int(raw, "reviewer_count", "Gold annotations"),
        has_overlap=_require_bool(raw, "has_overlap", "Gold annotations"),
        has_noise=_require_bool(raw, "has_noise", "Gold annotations"),
    )


def _require_exact_keys(
    record: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    if set(record) != allowed:
        raise ValueError(f"{label} contains unsupported or missing fields.")


def _require_string(
    record: Mapping[str, Any], key: str, label: str, *, allow_blank: bool = False
) -> str:
    value = record[key]
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        raise ValueError(f"{label} {key} must be a non-blank string.")
    return value


def _optional_string(record: Mapping[str, Any], key: str, label: str) -> str | None:
    value = record[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} {key} must be null or a non-blank string.")
    return value


def _require_int(record: Mapping[str, Any], key: str, label: str) -> int:
    value = record[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} {key} must be an integer.")
    return value


def _require_bool(record: Mapping[str, Any], key: str, label: str) -> bool:
    value = record[key]
    if not isinstance(value, bool):
        raise ValueError(f"{label} {key} must be boolean.")
    return value


def _parse_enum(enum_type: type[StrEnum], value: str, label: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid.") from error


def _require_non_blank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string.")


def _require_identifier(value: str, label: str) -> None:
    _require_non_blank(value, label)
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be an opaque identifier, not a path.")


def _require_timestamp_interval(start_ms: int, end_ms: int) -> None:
    if (
        not isinstance(start_ms, int)
        or isinstance(start_ms, bool)
        or not isinstance(end_ms, int)
        or isinstance(end_ms, bool)
        or start_ms < 0
        or end_ms <= start_ms
    ):
        raise ValueError(
            "Gold timestamps must form a non-empty [start_ms, end_ms) interval."
        )


def _is_valid_tag(tag: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", tag))


def _normalized_phrase_present(phrase: str, text: str) -> bool:
    return _phrase_tokens_present(
        normalize_german_tokens(phrase), normalize_german_tokens(text)
    )


def _phrase_tokens_present(phrase: Sequence[str], text: Sequence[str]) -> bool:
    return bool(phrase) and any(
        tuple(text[index : index + len(phrase)]) == tuple(phrase)
        for index in range(len(text) - len(phrase) + 1)
    )


def _raw_phrase_present(phrase: str, text: str) -> bool:
    return (
        re.search(rf"(?<![^\W_]){re.escape(phrase)}(?![^\W_])", text, flags=re.UNICODE)
        is not None
    )


def _closest_token_window(
    expected: Sequence[str], observed: Sequence[str]
) -> str | None:
    if not observed:
        return None
    width = min(len(expected), len(observed))
    best_window = min(
        (
            tuple(observed[index : index + width])
            for index in range(len(observed) - width + 1)
        ),
        key=lambda window: _calculate_error_counts(expected, window).substitutions
        + _calculate_error_counts(expected, window).deletions
        + _calculate_error_counts(expected, window).insertions,
    )
    return " ".join(best_window)


def _find_first_phrase_span(
    tokens: Sequence[str],
    phrase: Sequence[str],
    *,
    excluded_indices: set[int],
) -> tuple[int, int] | None:
    if not phrase:
        return None
    for start in range(len(tokens) - len(phrase) + 1):
        end = start + len(phrase)
        if any(index in excluded_indices for index in range(start, end)):
            continue
        if tuple(tokens[start:end]) == tuple(phrase):
            return start, end
    return None


def _is_number_token(token: str) -> bool:
    return token.isdecimal() or token in _GERMAN_NUMBER_WORDS
