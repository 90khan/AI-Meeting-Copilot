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
from app.application.interfaces.recording_key_store import RecordingKeyStore
from app.application.interfaces.recording_repository import RecordingRepository
from app.application.interfaces.unit_of_work import UnitOfWork
from app.application.interfaces.unit_of_work_factory import UnitOfWorkFactory

__all__ = [
    "GermanSimplificationProvider",
    "GermanSimplificationProviderFactory",
    "LiveTranscriptionSession",
    "MeetingSummarizationProvider",
    "MeetingSummarizationProviderFactory",
    "RecordingKeyStore",
    "RecordingRepository",
    "ReplyCoachingProvider",
    "ReplyCoachingProviderFactory",
    "SpeechToTextProvider",
    "SpeechToTextProviderFactory",
    "TranslationProvider",
    "TranslationProviderFactory",
    "UnitOfWork",
    "UnitOfWorkFactory",
]
