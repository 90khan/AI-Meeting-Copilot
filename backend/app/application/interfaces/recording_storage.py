"""Application contracts for encrypted local recording segment storage."""

from typing import Protocol
from uuid import UUID

from app.application.dto.recordings.encryption import RecordingKeyReference
from app.application.dto.recordings.storage import RecordingSegmentDescriptor


class RecordingSegmentWriter(Protocol):
    async def write(self, data: bytes) -> None: ...

    async def finalize(self) -> RecordingSegmentDescriptor: ...

    async def abort(self) -> None: ...


class RecordingStorage(Protocol):
    async def create_segment_writer(
        self,
        recording_id: UUID,
        segment_index: int,
        key_reference: RecordingKeyReference,
    ) -> RecordingSegmentWriter: ...

    async def list_segments(
        self, recording_id: UUID
    ) -> tuple[RecordingSegmentDescriptor, ...]: ...

    async def delete_recording(self, recording_id: UUID) -> None: ...

    async def recording_exists(self, recording_id: UUID) -> bool: ...
