"""Deterministic, private-only report helpers for Quality O gold STT runs.

This module serializes results already produced by ``gold_stt_dataset``.  It
does not load audio, invoke an STT provider, or import live application paths.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from app.development.gold_stt_dataset import (
    GoldAlignedSttReport,
    SttPredictionDataset,
    TextErrorCounts,
)

_REPORT_SCHEMA_VERSION = 1
_PRIVATE_WORKSPACE_NAME = "quality-private"


def build_gold_stt_baseline_report(
    *,
    evaluation: GoldAlignedSttReport,
    predictions: SttPredictionDataset,
) -> dict[str, object]:
    """Build a stable report containing IDs and metrics, never transcript text."""

    return {
        "report_schema_version": _REPORT_SCHEMA_VERSION,
        "audio_id": evaluation.audio_id,
        "prediction_run": {
            "run_id": predictions.run_id,
            "model_label": predictions.model_label,
        },
        "evaluation": {
            "evaluated_segment_count": evaluation.scored_segment_count,
            "evaluated_audio_duration_ms": evaluation.evaluated_audio_duration_ms,
        },
        "aggregate": {
            "wer": evaluation.word_errors.error_rate,
            "cer": evaluation.character_errors.error_rate,
            "word_errors": _text_error_report(evaluation.word_errors),
            "character_errors": _text_error_report(evaluation.character_errors),
        },
        "technical_terms": {
            "occurrence_count": evaluation.technical_terms.occurrence_count,
            "exact_match_count": evaluation.technical_terms.exact_match_count,
            "normalized_match_count": (
                evaluation.technical_terms.normalized_match_count
            ),
            "recall": evaluation.technical_terms.recall,
            "confusion_count": sum(
                item.occurrence_count for item in evaluation.technical_terms.confusions
            ),
        },
        "numbers": {
            "occurrence_count": evaluation.numbers.occurrence_count,
            "surface_match_count": evaluation.numbers.surface_match_count,
            "value_match_count": evaluation.numbers.value_match_count,
            "substitutions": evaluation.numbers.substitutions,
            "deletions": evaluation.numbers.deletions,
            "insertions": evaluation.numbers.insertions,
        },
        "per_segment": [
            {
                "segment_id": item.segment_id,
                "duration_ms": item.duration_ms,
                "wer": item.word_errors.error_rate,
                "cer": item.character_errors.error_rate,
                "word_errors": _text_error_report(item.word_errors),
                "character_errors": _text_error_report(item.character_errors),
            }
            for item in evaluation.per_segment
        ],
    }


def render_gold_stt_baseline_report(report: dict[str, object]) -> str:
    """Render canonical JSON without volatile timestamps or private text."""

    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def require_protected_quality_workspace(
    *,
    paths: Sequence[Path],
    repository_root: Path,
    is_git_ignored: Callable[[Path], bool],
) -> Path:
    """Require all files to share one Git-protected ``quality-private`` tree.

    A workspace outside this repository is protected from this repository's
    Git history.  A workspace inside it must be confirmed ignored before the
    caller reads private input or writes an output report.
    """

    if not paths:
        raise ValueError("Quality O requires private input and output paths.")
    resolved_repository_root = repository_root.resolve()
    workspaces = {_find_private_workspace(path.resolve()) for path in paths}
    if None in workspaces or len(workspaces) != 1:
        raise ValueError(
            "Quality O paths must share one protected quality-private workspace."
        )
    workspace = next(iter(workspaces))
    assert workspace is not None
    if _is_within(workspace, resolved_repository_root) and not is_git_ignored(
        workspace
    ):
        raise ValueError("The Quality O private workspace is not protected from Git.")
    return workspace


def _text_error_report(errors: TextErrorCounts) -> dict[str, int]:
    return {
        "reference_count": errors.reference_count,
        "hypothesis_count": errors.hypothesis_count,
        "substitutions": errors.substitutions,
        "deletions": errors.deletions,
        "insertions": errors.insertions,
    }


def _find_private_workspace(path: Path) -> Path | None:
    """Return the nearest required workspace ancestor, if one exists."""

    for candidate in (path, *path.parents):
        if candidate.name == _PRIVATE_WORKSPACE_NAME:
            return candidate
    return None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
