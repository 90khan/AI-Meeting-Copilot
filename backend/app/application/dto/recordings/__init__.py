"""Recording metadata DTOs."""

from app.application.dto.recordings.cleanup import (
    RecordingCleanupItemResult,
    RecordingCleanupOutcome,
    RecordingCleanupResult,
)
from app.application.dto.recordings.encryption import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)
from app.application.dto.recordings.recording import (
    RecordingDeletionStatus,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingState,
)
from app.application.dto.recordings.recording_session import (
    FinalizeRecordingCommand,
    MarkRecordingFailedCommand,
    MarkRecordingStartedCommand,
    PrepareRecordingSessionCommand,
    PrepareRecordingSessionResult,
)
from app.application.dto.recordings.retention import RecordingRetentionPolicy
from app.application.dto.recordings.storage import RecordingSegmentDescriptor

__all__ = [
    "FinalizeRecordingCommand",
    "MarkRecordingFailedCommand",
    "MarkRecordingStartedCommand",
    "PrepareRecordingSessionCommand",
    "PrepareRecordingSessionResult",
    "RecordingCleanupItemResult",
    "RecordingCleanupOutcome",
    "RecordingCleanupResult",
    "RecordingDeletionStatus",
    "RecordingEncryptionKey",
    "RecordingKeyReference",
    "RecordingMetadata",
    "RecordingMetadataRecord",
    "RecordingRetentionPolicy",
    "RecordingSegmentDescriptor",
    "RecordingState",
]
