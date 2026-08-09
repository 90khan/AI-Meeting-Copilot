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
    RECORDING_SEGMENT_DURATION_SECONDS,
    RECORDING_SEGMENT_OVERLAP_SECONDS,
    RecordingDeletionStatus,
    RecordingMediaFormat,
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
    WriteRecordingSegmentCommand,
    WriteRecordingSegmentResult,
)
from app.application.dto.recordings.retention import RecordingRetentionPolicy
from app.application.dto.recordings.retention_update import (
    UpdateAudioRetentionCommand,
    UpdateAudioRetentionResult,
)
from app.application.dto.recordings.storage import RecordingSegmentDescriptor

__all__ = [
    "RECORDING_SEGMENT_DURATION_SECONDS",
    "RECORDING_SEGMENT_OVERLAP_SECONDS",
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
    "RecordingMediaFormat",
    "RecordingMetadata",
    "RecordingMetadataRecord",
    "RecordingPlaybackInfo",
    "RecordingPlaybackSegment",
    "RecordingRetentionPolicy",
    "RecordingSegmentDescriptor",
    "RecordingState",
    "UpdateAudioRetentionCommand",
    "UpdateAudioRetentionResult",
    "WriteRecordingSegmentCommand",
    "WriteRecordingSegmentResult",
]
