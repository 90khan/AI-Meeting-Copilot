"""Evaluate explicit private Quality O gold and prediction JSONL files."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

from app.development.gold_stt_baseline import (
    build_gold_stt_baseline_report,
    render_gold_stt_baseline_report,
    require_protected_quality_workspace,
)
from app.development.gold_stt_dataset import (
    evaluate_gold_aligned_stt,
    load_gold_stt_dataset,
    load_stt_prediction_dataset,
)


def main(arguments: Sequence[str] | None = None) -> int:
    """Create one deterministic report without loading audio or calling STT."""

    parsed = _parse_arguments(arguments)
    repository_root = Path(__file__).resolve().parents[2]
    require_protected_quality_workspace(
        paths=(parsed.gold, parsed.predictions, parsed.output),
        repository_root=repository_root,
        is_git_ignored=lambda workspace: _is_git_ignored(repository_root, workspace),
    )
    if parsed.output.exists() and not parsed.overwrite:
        raise FileExistsError(
            "Quality O report already exists; use --overwrite to replace it."
        )
    if not parsed.output.parent.is_dir():
        raise ValueError("Quality O report parent directory does not exist.")

    gold = load_gold_stt_dataset(parsed.gold)
    predictions = load_stt_prediction_dataset(parsed.predictions)
    evaluation = evaluate_gold_aligned_stt(gold=gold, predictions=predictions)
    report = build_gold_stt_baseline_report(
        evaluation=evaluation,
        predictions=predictions,
    )
    parsed.output.write_text(render_gold_stt_baseline_report(report), encoding="utf-8")
    print("Quality O baseline report written.")
    return 0


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline Quality O gold STT baseline evaluation."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing private Quality O report.",
    )
    return parser.parse_args(arguments)


def _is_git_ignored(repository_root: Path, workspace: Path) -> bool:
    """Confirm a repository-local workspace is ignored without displaying it."""

    relative_probe = (
        workspace.relative_to(repository_root) / ".quality-o-protection-probe"
    )
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            str(relative_probe),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
