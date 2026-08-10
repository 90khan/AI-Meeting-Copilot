"""Persistence contract for durable integer playback timing metadata."""

from typing import Protocol
from uuid import UUID

from app.application.dto.recordings.segment_timing import RecordingSegmentTiming


class RecordingSegmentTimingRepository(Protocol):
    async def save(self, timing: RecordingSegmentTiming) -> None: ...
    async def get(
        self, recording_id: UUID, segment_index: int
    ) -> RecordingSegmentTiming | None: ...
    async def list_for_recording(
        self, recording_id: UUID
    ) -> tuple[RecordingSegmentTiming, ...]: ...
    async def get_last(self, recording_id: UUID) -> RecordingSegmentTiming | None: ...
