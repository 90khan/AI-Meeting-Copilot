"""Tests for immutable recording cleanup result DTOs."""

from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingCleanupItemResult,
    RecordingCleanupOutcome,
    RecordingCleanupResult,
)
from app.application.exceptions import ApplicationValidationError


def _item(outcome: RecordingCleanupOutcome) -> RecordingCleanupItemResult:
    index = list(RecordingCleanupOutcome).index(outcome) + 1
    return RecordingCleanupItemResult(recording_id=UUID(int=index), outcome=outcome)


def test_outcomes_items_messages_and_immutability() -> None:
    assert [item.value for item in RecordingCleanupOutcome] == [
        "deleted",
        "skipped",
        "failed",
        "missing",
    ]
    item = RecordingCleanupItemResult(
        recording_id=UUID(int=1),
        outcome=RecordingCleanupOutcome.DELETED,
        message=" Recording deleted. ",
    )
    assert item.message == "Recording deleted."
    with pytest.raises(AttributeError):
        item.message = None  # type: ignore[misc]
    for message in ("", "/tmp/file", "secret token", "native exception"):
        with pytest.raises(ApplicationValidationError):
            RecordingCleanupItemResult(
                recording_id=UUID(int=1),
                outcome=RecordingCleanupOutcome.FAILED,
                message=message,
            )


def test_result_counts_and_from_items() -> None:
    items = tuple(_item(outcome) for outcome in RecordingCleanupOutcome)
    result = RecordingCleanupResult.from_items(item for item in items)
    assert result.checked_count == 4
    assert result.deleted_count == result.skipped_count == 1
    assert result.failed_count == result.missing_count == 1
    assert result.items == items
    with pytest.raises(AttributeError):
        result.items = ()  # type: ignore[misc]
    with pytest.raises(ApplicationValidationError):
        RecordingCleanupResult(
            checked_count=-1,
            deleted_count=0,
            skipped_count=0,
            failed_count=0,
            missing_count=0,
            items=(),
        )
    with pytest.raises(ApplicationValidationError):
        RecordingCleanupResult(
            checked_count=4,
            deleted_count=0,
            skipped_count=0,
            failed_count=0,
            missing_count=0,
            items=items,
        )
    with pytest.raises(ApplicationValidationError):
        RecordingCleanupResult(
            checked_count=4,
            deleted_count=1,
            skipped_count=1,
            failed_count=1,
            missing_count=1,
            items=list(items),  # type: ignore[arg-type]
        )
