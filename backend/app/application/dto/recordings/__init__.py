"""Recording metadata DTOs."""

from app.application.dto.recordings.cleanup import (
    RecordingCleanupItemResult,
    RecordingCleanupOutcome,
    RecordingCleanupResult,
)
from app.application.dto.recordings.deletion import (
    DeleteMeetingAudioCommand,
    DeleteMeetingAudioResult,
)
from app.application.dto.recordings.encryption import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)
from app.application.dto.recordings.playback import (
    RecordingPlaybackInfo,
    RecordingPlaybackSegment,
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
from app.application.dto.recordings.retention_update import (
    UpdateAudioRetentionCommand,
    UpdateAudioRetentionResult,
)
from app.application.dto.recordings.storage import RecordingSegmentDescriptor

__all__ = [
    "DeleteMeetingAudioCommand",
    "DeleteMeetingAudioResult",
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
    "RecordingPlaybackInfo",
    "RecordingPlaybackSegment",
    "RecordingRetentionPolicy",
    "RecordingSegmentDescriptor",
    "RecordingState",
    "UpdateAudioRetentionCommand",
    "UpdateAudioRetentionResult",
]
