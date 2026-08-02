"""Application contract for local recording encryption-key storage."""

from typing import Protocol
from uuid import UUID

from app.application.dto.recordings.encryption import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)


class RecordingKeyStore(Protocol):
    """Store raw recording keys outside application persistence."""

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference: ...

    async def get_key(
        self, reference: RecordingKeyReference
    ) -> RecordingEncryptionKey: ...

    async def delete_key(self, reference: RecordingKeyReference) -> None: ...

    async def exists(self, reference: RecordingKeyReference) -> bool: ...
