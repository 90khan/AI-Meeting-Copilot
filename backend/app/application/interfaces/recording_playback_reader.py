"""Internal sequential read contract for trusted local audio playback."""

from collections.abc import AsyncIterator
from typing import Protocol

from app.application.dto.recordings.playback import (
    RecordingPlaybackInfo,
    RecordingPlaybackSegment,
)
from app.domain.value_objects import MeetingId


class RecordingPlaybackReader(Protocol):
    """Read completed encrypted recordings without exposing storage details."""

    async def get_info(self, meeting_id: MeetingId) -> RecordingPlaybackInfo: ...

    def read_segments(
        self,
        meeting_id: MeetingId,
    ) -> AsyncIterator[RecordingPlaybackSegment]: ...
