"""Synthetic tests for the private-only Quality O baseline runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.development.gold_stt_baseline import require_protected_quality_workspace
from scripts.run_gold_stt_baseline import _is_git_ignored, main


def _gold_record(
    *,
    segment_id: str = "segment-001",
    start_ms: int = 0,
    end_ms: int = 1_000,
    text_de: str = "RAG benötigt fünfundzwanzig Tests.",
    reference_state: str = "verified",
    evaluation_eligible: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "audio_id": "synthetic-audio-1",
        "segment_id": segment_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speaker": "speaker_1",
        "text_de": text_de,
        "normalized_text_de": None,
        "reference_state": reference_state,
        "evaluation_eligible": evaluation_eligible,
        "tags": ["synthetic"],
        "terms": [
            {
                "surface": "RAG",
                "canonical": "RAG",
                "category": "technical_term",
            }
        ],
        "numbers": [
            {
                "surface": "fünfundzwanzig",
                "canonical_value": "25",
                "kind": "cardinal",
                "unit": None,
                "value_forms": [],
            }
        ],
        "annotations": {
            "review_status": "verified",
            "reviewer_count": 1,
            "has_overlap": False,
            "has_noise": False,
        },
    }


def _prediction_record(
    *,
    segment_id: str = "segment-001",
    hypothesis_de: str = "RAG benötigt 25 Tests.",
    run_id: str = "synthetic-run-1",
    model_label: str = "synthetic-model",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "audio_id": "synthetic-audio-1",
        "segment_id": segment_id,
        "run_id": run_id,
        "model_label": model_label,
        "hypothesis_de": hypothesis_de,
    }


def _write_jsonl(path: Path, records: tuple[dict[str, object], ...]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _private_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "quality-private"
    gold_directory = workspace / "gold"
    prediction_directory = workspace / "predictions"
    report_directory = workspace / "reports"
    for directory in (gold_directory, prediction_directory, report_directory):
        directory.mkdir(parents=True, exist_ok=True)
    return (
        gold_directory / "gold.jsonl",
        prediction_directory / "predictions.jsonl",
        report_directory / "baseline.json",
    )


def _run(gold: Path, predictions: Path, output: Path, *extra: str) -> int:
    return main(
        (
            "--gold",
            str(gold),
            "--predictions",
            str(predictions),
            "--output",
            str(output),
            *extra,
        )
    )


def test_cli_writes_a_deterministic_private_metric_report(tmp_path: Path) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    _write_jsonl(gold, (_gold_record(),))
    _write_jsonl(predictions, (_prediction_record(),))

    assert _run(gold, predictions, output) == 0
    first_report = output.read_text(encoding="utf-8")
    assert _run(gold, predictions, output, "--overwrite") == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert output.read_text(encoding="utf-8") == first_report
    assert report["report_schema_version"] == 1
    assert report["prediction_run"] == {
        "model_label": "synthetic-model",
        "run_id": "synthetic-run-1",
    }
    assert report["evaluation"] == {
        "evaluated_audio_duration_ms": 1_000,
        "evaluated_segment_count": 1,
    }
    assert report["aggregate"]["word_errors"] == {
        "deletions": 0,
        "hypothesis_count": 4,
        "insertions": 0,
        "reference_count": 4,
        "substitutions": 1,
    }
    assert report["technical_terms"]["normalized_match_count"] == 1
    assert report["numbers"]["value_match_count"] == 1
    assert report["numbers"]["surface_match_count"] == 0
    assert "benötigt" not in first_report
    assert "Tests" not in first_report
    assert "RAG benötigt fünfundzwanzig Tests." not in first_report
    assert "RAG benötigt 25 Tests." not in first_report
    assert "/private/" not in first_report


def test_cli_rejects_malformed_gold_before_writing_report(tmp_path: Path) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    malformed_gold = _gold_record()
    malformed_gold["audio_path"] = "not-allowed"
    _write_jsonl(gold, (malformed_gold,))
    _write_jsonl(predictions, (_prediction_record(),))

    with pytest.raises(ValueError, match="Gold record contains"):
        _run(gold, predictions, output)

    assert not output.exists()


def test_cli_rejects_malformed_prediction_before_writing_report(tmp_path: Path) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    malformed_prediction = _prediction_record()
    malformed_prediction["text_de"] = "must-not-be-gold"
    _write_jsonl(gold, (_gold_record(),))
    _write_jsonl(predictions, (malformed_prediction,))

    with pytest.raises(ValueError, match="Prediction record contains"):
        _run(gold, predictions, output)

    assert not output.exists()


def test_cli_requires_predictions_for_every_eligible_segment(tmp_path: Path) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    _write_jsonl(
        gold,
        (
            _gold_record(),
            _gold_record(segment_id="segment-002", start_ms=1_000, end_ms=2_000),
        ),
    )
    _write_jsonl(predictions, (_prediction_record(),))

    with pytest.raises(ValueError, match="missing eligible"):
        _run(gold, predictions, output)

    assert not output.exists()


def test_cli_excludes_non_eligible_gold_from_primary_metrics(tmp_path: Path) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    _write_jsonl(
        gold,
        (
            _gold_record(),
            {
                **_gold_record(
                    segment_id="segment-002",
                    start_ms=1_000,
                    end_ms=2_000,
                    text_de="Unsichere Worte.",
                    reference_state="uncertain",
                    evaluation_eligible=False,
                ),
                "terms": [],
                "numbers": [],
            },
        ),
    )
    _write_jsonl(
        predictions,
        (
            _prediction_record(),
            _prediction_record(segment_id="segment-002", hypothesis_de="falsch"),
        ),
    )

    _run(gold, predictions, output)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["evaluation"]["evaluated_segment_count"] == 1
    assert [item["segment_id"] for item in report["per_segment"]] == ["segment-001"]


def test_cli_requires_explicit_overwrite_and_preserves_existing_report(
    tmp_path: Path,
) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    _write_jsonl(gold, (_gold_record(),))
    _write_jsonl(predictions, (_prediction_record(),))
    output.write_text("existing private report\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--overwrite"):
        _run(gold, predictions, output)

    assert output.read_text(encoding="utf-8") == "existing private report\n"
    _run(gold, predictions, output, "--overwrite")
    assert output.read_text(encoding="utf-8").startswith("{")


def test_multiple_prediction_runs_can_reuse_the_same_gold(tmp_path: Path) -> None:
    gold, predictions, output = _private_paths(tmp_path)
    second_predictions = predictions.with_name("predictions-run-2.jsonl")
    second_output = output.with_name("baseline-run-2.json")
    _write_jsonl(gold, (_gold_record(),))
    _write_jsonl(predictions, (_prediction_record(),))
    _write_jsonl(
        second_predictions,
        (_prediction_record(run_id="synthetic-run-2", model_label="candidate-model"),),
    )

    _run(gold, predictions, output)
    _run(gold, second_predictions, second_output)

    assert (
        json.loads(output.read_text(encoding="utf-8"))["prediction_run"]["run_id"]
        == "synthetic-run-1"
    )
    assert (
        json.loads(second_output.read_text(encoding="utf-8"))["prediction_run"][
            "run_id"
        ]
        == "synthetic-run-2"
    )


def test_private_workspace_inside_repository_must_be_git_ignored(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    workspace = repository_root / "quality-private"
    paths = (
        workspace / "gold" / "gold.jsonl",
        workspace / "predictions" / "predictions.jsonl",
        workspace / "reports" / "baseline.json",
    )

    def ignored(_: Path) -> bool:
        return False

    with pytest.raises(ValueError, match="not protected"):
        require_protected_quality_workspace(
            paths=paths,
            repository_root=repository_root,
            is_git_ignored=ignored,
        )


def test_repository_local_quality_private_workspace_is_git_ignored() -> None:
    repository_root = Path(__file__).resolve().parents[3]

    assert _is_git_ignored(repository_root, repository_root / "quality-private")


def test_private_paths_must_share_one_named_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "quality-private"
    other_workspace = tmp_path / "other-quality-private"

    with pytest.raises(ValueError, match="share one protected"):
        require_protected_quality_workspace(
            paths=(
                workspace / "gold" / "gold.jsonl",
                workspace / "predictions" / "predictions.jsonl",
                other_workspace / "reports" / "baseline.json",
            ),
            repository_root=tmp_path / "repository",
            is_git_ignored=lambda _: True,
        )
