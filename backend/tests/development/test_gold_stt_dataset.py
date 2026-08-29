"""Synthetic tests for private, offline Quality O gold STT evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.development.gold_stt_dataset import (
    GoldNumber,
    GoldNumberKind,
    GoldReferenceState,
    GoldReviewMetadata,
    GoldReviewStatus,
    GoldSttDataset,
    GoldSttSegment,
    GoldTechnicalTerm,
    GoldTermCategory,
    NumberEvaluationOutcome,
    SttPrediction,
    SttPredictionDataset,
    evaluate_gold_aligned_stt,
    load_gold_stt_dataset,
    load_stt_prediction_dataset,
    normalize_german_text,
    normalize_german_tokens,
)


def _annotations() -> GoldReviewMetadata:
    return GoldReviewMetadata(
        review_status=GoldReviewStatus.VERIFIED,
        reviewer_count=1,
    )


def _segment(
    *,
    segment_id: str = "gold-001",
    start_ms: int = 0,
    end_ms: int = 1_000,
    text_de: str = "RAG ist wichtig.",
    reference_state: GoldReferenceState = GoldReferenceState.VERIFIED,
    evaluation_eligible: bool = True,
    terms: tuple[GoldTechnicalTerm, ...] = (),
    numbers: tuple[GoldNumber, ...] = (),
) -> GoldSttSegment:
    return GoldSttSegment(
        schema_version=1,
        audio_id="synthetic-audio-1",
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker="speaker_1",
        text_de=text_de,
        normalized_text_de=None,
        reference_state=reference_state,
        evaluation_eligible=evaluation_eligible,
        tags=("german",),
        terms=terms,
        numbers=numbers,
        annotations=_annotations(),
    )


def _predictions(*items: tuple[str, str]) -> SttPredictionDataset:
    return SttPredictionDataset(
        audio_id="synthetic-audio-1",
        run_id="synthetic-run-1",
        model_label="synthetic-model",
        predictions=tuple(
            SttPrediction(
                schema_version=1,
                audio_id="synthetic-audio-1",
                segment_id=segment_id,
                run_id="synthetic-run-1",
                model_label="synthetic-model",
                hypothesis_de=text,
            )
            for segment_id, text in items
        ),
    )


def _gold_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "audio_id": "synthetic-audio-1",
        "segment_id": "gold-001",
        "start_ms": 0,
        "end_ms": 1_000,
        "speaker": "speaker_1",
        "text_de": "RAG ist wichtig.",
        "normalized_text_de": None,
        "reference_state": "verified",
        "evaluation_eligible": True,
        "tags": ["german", "technical_term"],
        "terms": [
            {
                "surface": "RAG",
                "canonical": "RAG",
                "category": "technical_term",
            }
        ],
        "numbers": [],
        "annotations": {
            "review_status": "verified",
            "reviewer_count": 1,
            "has_overlap": False,
            "has_noise": False,
        },
    }
    record.update(overrides)
    return record


def _write_jsonl(path: Path, *records: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_valid_gold_jsonl_loads_with_derived_normalization(tmp_path: Path) -> None:
    path = tmp_path / "gold.jsonl"
    _write_jsonl(path, _gold_record())

    dataset = load_gold_stt_dataset(path)

    assert dataset.audio_id == "synthetic-audio-1"
    assert dataset.eligible_segments[0].derived_normalized_text_de == "rag ist wichtig"


@pytest.mark.parametrize(
    ("start_ms", "end_ms"),
    [(1_000, 1_000), (1_000, 999), (-1, 100)],
)
def test_gold_segment_rejects_invalid_half_open_timestamps(
    start_ms: int, end_ms: int
) -> None:
    with pytest.raises(ValueError, match="timestamps"):
        _segment(start_ms=start_ms, end_ms=end_ms)


def test_gold_dataset_rejects_duplicate_segment_ids_and_out_of_order_intervals() -> (
    None
):
    with pytest.raises(ValueError, match="segment IDs"):
        GoldSttDataset(
            audio_id="synthetic-audio-1",
            segments=(_segment(), _segment(start_ms=1_000, end_ms=2_000)),
        )
    with pytest.raises(ValueError, match="ordered"):
        GoldSttDataset(
            audio_id="synthetic-audio-1",
            segments=(
                _segment(segment_id="gold-001", start_ms=1_000, end_ms=2_000),
                _segment(segment_id="gold-002", start_ms=1_500, end_ms=2_500),
            ),
        )


def test_primary_metrics_include_only_verified_eligible_gold() -> None:
    verified = _segment(segment_id="gold-001", text_de="eins zwei")
    excluded = _segment(
        segment_id="gold-002",
        start_ms=1_000,
        end_ms=2_000,
        text_de="drei vier",
        reference_state=GoldReferenceState.UNCERTAIN,
        evaluation_eligible=False,
    )
    gold = GoldSttDataset(audio_id="synthetic-audio-1", segments=(verified, excluded))

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "eins zwei"), ("gold-002", "falsch")),
    )

    assert report.scored_segment_count == 1
    assert report.evaluated_audio_duration_ms == 1_000
    assert report.word_errors.reference_count == 2


def test_normalization_is_deterministic_and_preserves_identifiers_and_compounds() -> (
    None
):
    decomposed_umlaut = "A\u0308pfel"

    assert normalize_german_tokens(
        f"RAG, BM25; HyDE — Re-Ranking und 30-minütiges {decomposed_umlaut}!"
    ) == ("rag", "bm25", "hyde", "re-ranking", "und", "30-minütiges", "äpfel")
    assert normalize_german_text("  RAG,\nHyDE! ") == "rag hyde"


def test_word_and_character_error_accounting_is_independent() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="eins zwei drei"),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "eins vier")),
    )

    assert report.word_errors.reference_count == 3
    assert report.word_errors.hypothesis_count == 2
    assert report.word_errors.substitutions == 1
    assert report.word_errors.deletions == 1
    assert report.word_errors.insertions == 0
    assert report.character_errors.reference_count == len("einszweidrei")
    assert report.character_errors.hypothesis_count == len("einsvier")


def test_cer_counts_substitution_and_insertion() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1", segments=(_segment(text_de="ab"),)
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "acx")),
    )

    assert report.character_errors.substitutions == 1
    assert report.character_errors.insertions == 1
    assert report.character_errors.deletions == 0


def test_terms_report_exact_and_normalized_matches() -> None:
    term = GoldTechnicalTerm(
        surface="RAG", canonical="RAG", category=GoldTermCategory.TECHNICAL_TERM
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1", segments=(_segment(terms=(term,)),)
    )

    exact = evaluate_gold_aligned_stt(
        gold=gold, predictions=_predictions(("gold-001", "RAG ist wichtig."))
    )
    normalized = evaluate_gold_aligned_stt(
        gold=gold, predictions=_predictions(("gold-001", "rag ist wichtig."))
    )

    assert exact.technical_terms.exact_match_count == 1
    assert normalized.technical_terms.exact_match_count == 0
    assert normalized.technical_terms.normalized_match_count == 1
    assert normalized.technical_terms.recall == 1.0


def test_terms_report_a_private_confusion_without_changing_gold() -> None:
    term = GoldTechnicalTerm(
        surface="RAG", canonical="RAG", category=GoldTermCategory.TECHNICAL_TERM
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1", segments=(_segment(terms=(term,)),)
    )

    report = evaluate_gold_aligned_stt(
        gold=gold, predictions=_predictions(("gold-001", "Rack ist wichtig."))
    )

    assert report.technical_terms.recall == 0.0
    assert report.technical_terms.confusions[0].canonical == "RAG"
    assert report.technical_terms.confusions[0].observed == "rack"


@pytest.mark.parametrize(
    ("term_surface", "hypothesis"),
    [
        ("RAG", "Rack ist wichtig."),
        ("BM25", "BME-25 ist ein Verfahren."),
        ("HyDE", "Height ist ein Verfahren."),
    ],
)
def test_term_confusions_remain_errors(term_surface: str, hypothesis: str) -> None:
    term = GoldTechnicalTerm(
        surface=term_surface,
        canonical=term_surface,
        category=GoldTermCategory.TECHNICAL_TERM,
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de=f"{term_surface} ist wichtig.", terms=(term,)),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", hypothesis)),
    )

    assert report.technical_terms.normalized_match_count == 0


def test_repeated_term_annotations_consume_hypothesis_occurrences() -> None:
    term = GoldTechnicalTerm(
        surface="RAG", canonical="RAG", category=GoldTermCategory.TECHNICAL_TERM
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="RAG und später RAG.", terms=(term, term)),),
    )

    one_observed = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "Wir verwenden RAG.")),
    )
    two_observed = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(
            ("gold-001", "RAG wird verwendet und später erneut RAG.")
        ),
    )

    assert one_observed.technical_terms.occurrence_count == 2
    assert one_observed.technical_terms.normalized_match_count == 1
    assert two_observed.technical_terms.normalized_match_count == 2


def test_mixed_repeated_terms_do_not_cross_consume_occurrences() -> None:
    rag = GoldTechnicalTerm(
        surface="RAG", canonical="RAG", category=GoldTermCategory.TECHNICAL_TERM
    )
    bm25 = GoldTechnicalTerm(
        surface="BM25", canonical="BM25", category=GoldTermCategory.TECHNICAL_TERM
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="RAG BM25 RAG", terms=(rag, bm25, rag)),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "RAG und BM25")),
    )

    assert [item.normalized_match for item in report.technical_terms.occurrences] == [
        True,
        True,
        False,
    ]


def test_unicode_and_hyphenated_terms_normalize_without_model_corrections() -> None:
    location = GoldTechnicalTerm(
        surface="Mörfelden-Walldorf",
        canonical="Mörfelden-Walldorf",
        category=GoldTermCategory.LOCATION,
    )
    reranking = GoldTechnicalTerm(
        surface="Re-Ranking",
        canonical="Re-Ranking",
        category=GoldTermCategory.TECHNICAL_TERM,
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(
            _segment(
                text_de="Mörfelden-Walldorf nutzt Re-Ranking.",
                terms=(location, reranking),
            ),
        ),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(
            ("gold-001", "Mo\u0308rfelden-Walldorf nutzt re-ranking.")
        ),
    )

    assert report.technical_terms.normalized_match_count == 2
    assert report.technical_terms.exact_match_count == 1


def test_numbers_distinguish_surface_and_annotation_defined_value_forms() -> None:
    number = GoldNumber(
        surface="fünfundzwanzig",
        canonical_value="25",
        kind=GoldNumberKind.CARDINAL,
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="fünfundzwanzig Personen", numbers=(number,)),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold, predictions=_predictions(("gold-001", "25 Personen"))
    )

    occurrence = report.numbers.occurrences[0]
    assert occurrence.surface_match is False
    assert occurrence.value_match is True
    assert occurrence.outcome is NumberEvaluationOutcome.VALUE_MATCH
    assert report.numbers.insertions == 0


def test_repeated_number_annotations_consume_surface_occurrences() -> None:
    number = GoldNumber(
        surface="neun",
        canonical_value="9",
        kind=GoldNumberKind.CARDINAL,
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="neun und neun Jahre", numbers=(number, number)),),
    )

    one_observed = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "seit neun Jahren")),
    )
    two_observed = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "seit neun und neun Jahren")),
    )

    assert one_observed.numbers.surface_match_count == 1
    assert one_observed.numbers.value_match_count == 1
    assert one_observed.numbers.deletions == 1
    assert two_observed.numbers.surface_match_count == 2
    assert two_observed.numbers.value_match_count == 2


def test_number_value_equivalence_remains_annotation_defined() -> None:
    number = GoldNumber(
        surface="neun",
        canonical_value="9",
        kind=GoldNumberKind.CARDINAL,
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="neun Jahre", numbers=(number,)),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "9 Jahre")),
    )

    assert report.numbers.surface_match_count == 0
    assert report.numbers.value_match_count == 1


def test_bm25_is_only_a_technical_term_not_a_standalone_number() -> None:
    term = GoldTechnicalTerm(
        surface="BM25", canonical="BM25", category=GoldTermCategory.TECHNICAL_TERM
    )
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="BM25 ist ein Verfahren.", terms=(term,)),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold, predictions=_predictions(("gold-001", "BM25 ist ein Verfahren."))
    )

    assert report.numbers.occurrence_count == 0
    assert report.numbers.insertions == 0
    assert report.technical_terms.normalized_match_count == 1


def test_gold_and_prediction_jsonl_are_strictly_separated(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.jsonl"
    prediction_path = tmp_path / "prediction.jsonl"
    _write_jsonl(gold_path, _gold_record(audio_path="/private/private.wav"))
    _write_jsonl(
        prediction_path,
        {
            "schema_version": 1,
            "audio_id": "synthetic-audio-1",
            "segment_id": "gold-001",
            "run_id": "synthetic-run-1",
            "model_label": "synthetic-model",
            "hypothesis_de": "RAG ist wichtig.",
            "text_de": "must not be accepted",
        },
    )

    with pytest.raises(ValueError, match="Gold record contains"):
        load_gold_stt_dataset(gold_path)
    with pytest.raises(ValueError, match="Prediction record contains"):
        load_stt_prediction_dataset(prediction_path)


def test_predictions_must_cover_all_eligible_gold_segments() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(
            _segment(segment_id="gold-001"),
            _segment(segment_id="gold-002", start_ms=1_000, end_ms=2_000),
        ),
    )

    with pytest.raises(ValueError, match="missing eligible"):
        evaluate_gold_aligned_stt(
            gold=gold,
            predictions=_predictions(("gold-001", "RAG ist wichtig.")),
        )


def test_predictions_reject_unknown_segment_ids_and_audio_mismatches() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(),),
    )
    unknown = _predictions(("unknown-001", "RAG ist wichtig."))
    mismatched_audio = SttPredictionDataset(
        audio_id="synthetic-audio-2",
        run_id="synthetic-run-1",
        model_label="synthetic-model",
        predictions=(
            SttPrediction(
                schema_version=1,
                audio_id="synthetic-audio-2",
                segment_id="gold-001",
                run_id="synthetic-run-1",
                model_label="synthetic-model",
                hypothesis_de="RAG ist wichtig.",
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown gold"):
        evaluate_gold_aligned_stt(gold=gold, predictions=unknown)
    with pytest.raises(ValueError, match="audio IDs must match"):
        evaluate_gold_aligned_stt(gold=gold, predictions=mismatched_audio)


def test_prediction_dataset_rejects_duplicate_segment_ids() -> None:
    prediction = SttPrediction(
        schema_version=1,
        audio_id="synthetic-audio-1",
        segment_id="gold-001",
        run_id="synthetic-run-1",
        model_label="synthetic-model",
        hypothesis_de="RAG ist wichtig.",
    )

    with pytest.raises(ValueError, match="segment IDs must be unique"):
        SttPredictionDataset(
            audio_id="synthetic-audio-1",
            run_id="synthetic-run-1",
            model_label="synthetic-model",
            predictions=(prediction, prediction),
        )


def test_prediction_order_does_not_change_evaluation_metrics() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(
            _segment(segment_id="gold-001", text_de="eins zwei"),
            _segment(
                segment_id="gold-002",
                start_ms=1_000,
                end_ms=2_000,
                text_de="drei vier",
            ),
        ),
    )
    ordered = _predictions(("gold-001", "eins zwei"), ("gold-002", "drei"))
    reversed_predictions = _predictions(("gold-002", "drei"), ("gold-001", "eins zwei"))

    assert (
        evaluate_gold_aligned_stt(gold=gold, predictions=ordered).word_errors
        == evaluate_gold_aligned_stt(
            gold=gold, predictions=reversed_predictions
        ).word_errors
    )


def test_aggregate_wer_uses_summed_counts_not_segment_averages() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(
            _segment(segment_id="gold-001", text_de="eins"),
            _segment(
                segment_id="gold-002",
                start_ms=1_000,
                end_ms=2_000,
                text_de="zwei drei vier fünf sechs sieben acht neun zehn elf",
            ),
        ),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(
            ("gold-001", "falsch"),
            ("gold-002", "zwei drei vier fünf sechs sieben acht neun zehn falsch"),
        ),
    )

    assert report.word_errors.error_rate == pytest.approx(2 / 11)
    assert report.per_segment[0].word_errors.error_rate == 1.0
    assert report.per_segment[1].word_errors.error_rate == pytest.approx(1 / 10)


def test_empty_hypothesis_is_counted_as_deletions() -> None:
    gold = GoldSttDataset(
        audio_id="synthetic-audio-1",
        segments=(_segment(text_de="eins zwei drei"),),
    )

    report = evaluate_gold_aligned_stt(
        gold=gold,
        predictions=_predictions(("gold-001", "")),
    )

    assert report.word_errors.deletions == 3
    assert report.word_errors.error_rate == 1.0


def test_verified_normalized_text_must_equal_the_derived_evaluation_form() -> None:
    with pytest.raises(ValueError, match="normalized text"):
        GoldSttSegment(
            schema_version=1,
            audio_id="synthetic-audio-1",
            segment_id="gold-001",
            start_ms=0,
            end_ms=1_000,
            speaker=None,
            text_de="RAG ist wichtig.",
            normalized_text_de="incorrect",
            reference_state=GoldReferenceState.VERIFIED,
            evaluation_eligible=True,
            tags=("german",),
            terms=(),
            numbers=(),
            annotations=_annotations(),
        )
