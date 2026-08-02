"""Command for validating a live-transcription session start."""

from dataclasses import dataclass

from app.application.dto.ai import LanguageCode
from app.application.dto.live_transcription import AudioSource
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class StartLiveTranscriptionSessionCommand:
    """Carry the requested live-transcription configuration for one Meeting."""

    meeting_id: MeetingId
    language_hint: LanguageCode | None
    source: AudioSource
