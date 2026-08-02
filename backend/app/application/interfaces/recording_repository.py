"""Application contract for local recording metadata persistence."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.dto.recordings import RecordingMetadataRecord
from app.domain.value_objects import MeetingId


class RecordingRepository(Protocol):
    """Persist recording metadata without owning transactions or audio files."""

    async def save(self, metadata: RecordingMetadataRecord) -> None: ...

    async def get_by_id(self, recording_id: UUID) -> RecordingMetadataRecord | None: ...

    async def get_by_meeting_id(
        self, meeting_id: MeetingId
    ) -> RecordingMetadataRecord | None: ...

    async def list_all(self) -> tuple[RecordingMetadataRecord, ...]: ...

    async def list_expired(
        self, as_of: datetime
    ) -> tuple[RecordingMetadataRecord, ...]: ...

    async def delete_metadata(self, recording_id: UUID) -> None: ...
