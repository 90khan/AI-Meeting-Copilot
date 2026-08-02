"""Thin application boundary for recording-storage reconciliation."""

from app.application.dto.recordings import RecordingCleanupResult
from app.application.services.recording_storage_reconciler import (
    RecordingStorageReconciler,
)


class ReconcileRecordingStorageUseCase:
    """Delegate one reconciliation pass without adding orchestration behavior."""

    def __init__(self, reconciler: RecordingStorageReconciler) -> None:
        self._reconciler = reconciler

    async def execute(self) -> RecordingCleanupResult:
        return await self._reconciler.reconcile()
