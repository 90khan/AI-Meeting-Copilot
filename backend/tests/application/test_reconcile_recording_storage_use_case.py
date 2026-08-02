"""Tests for the thin reconciliation use-case boundary."""

import asyncio

from app.application.dto.recordings import RecordingCleanupResult
from app.application.use_cases import ReconcileRecordingStorageUseCase


class _Reconciler:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(self) -> RecordingCleanupResult:
        self.calls += 1
        return RecordingCleanupResult.from_items(())


def test_use_case_delegates_exactly_once() -> None:
    reconciler = _Reconciler()
    use_case = ReconcileRecordingStorageUseCase(reconciler)
    assert asyncio.run(use_case.execute()).checked_count == 0
    assert reconciler.calls == 1
