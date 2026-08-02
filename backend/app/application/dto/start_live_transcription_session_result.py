"""Validated configuration for a live-transcription session boundary."""

from dataclasses import dataclass

from app.application.dto.ai import LanguageCode
from app.application.dto.live_transcription import AudioSource
from app.domain.value_objects import MeetingId


@dataclass(frozen=True, slots=True, kw_only=True)
class StartLiveTranscriptionSessionResult:
    """Return validated Meeting and capture configuration without transport state."""

    meeting_id: MeetingId
    language_hint: LanguageCode | None
    source: AudioSource
