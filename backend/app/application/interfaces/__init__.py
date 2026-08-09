"""Application-layer interfaces."""

from app.application.interfaces.ai import (
    GermanSimplificationProvider,
    GermanSimplificationProviderFactory,
    MeetingSummarizationProvider,
    MeetingSummarizationProviderFactory,
    ReplyCoachingProvider,
    ReplyCoachingProviderFactory,
    SpeechToTextProvider,
    SpeechToTextProviderFactory,
    TranslationProvider,
    TranslationProviderFactory,
)
from app.application.interfaces.live_transcription import LiveTranscriptionSession
from app.application.interfaces.meeting_review_artifact_repository import (
    MeetingReviewArtifactRepository,
)
from app.application.interfaces.meeting_review_repository import MeetingReviewRepository
from app.application.interfaces.meeting_translation_repository import (
    MeetingTranslationRepository,
)
from app.application.interfaces.recording_key_store import RecordingKeyStore
from app.application.interfaces.recording_playback_reader import RecordingPlaybackReader
from app.application.interfaces.recording_repository import RecordingRepository
from app.application.interfaces.recording_storage import (
    RecordingSegmentWriter,
    RecordingStorage,
)
from app.application.interfaces.unit_of_work import UnitOfWork
from app.application.interfaces.unit_of_work_factory import UnitOfWorkFactory

__all__ = [
    "GermanSimplificationProvider",
    "GermanSimplificationProviderFactory",
    "LiveTranscriptionSession",
    "MeetingReviewArtifactRepository",
    "MeetingReviewRepository",
    "MeetingSummarizationProvider",
    "MeetingSummarizationProviderFactory",
    "MeetingTranslationRepository",
    "RecordingKeyStore",
    "RecordingPlaybackReader",
    "RecordingRepository",
    "RecordingSegmentWriter",
    "RecordingStorage",
    "ReplyCoachingProvider",
    "ReplyCoachingProviderFactory",
    "SpeechToTextProvider",
    "SpeechToTextProviderFactory",
    "TranslationProvider",
    "TranslationProviderFactory",
    "UnitOfWork",
    "UnitOfWorkFactory",
]
