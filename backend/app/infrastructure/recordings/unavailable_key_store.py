"""Platform-neutral unavailable recording key-store adapter."""

from uuid import UUID

from app.application.dto.recordings import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)
from app.application.exceptions import RecordingKeyUnavailableError


class UnavailableRecordingKeyStore:
    """Fail only when an unsupported host attempts a recording key operation."""

    async def create_key(self, recording_id: UUID) -> RecordingKeyReference:
        del recording_id
        raise RecordingKeyUnavailableError("Recording key storage is unavailable.")

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        del reference
        raise RecordingKeyUnavailableError("Recording key storage is unavailable.")

    async def delete_key(self, reference: RecordingKeyReference) -> None:
        del reference
        raise RecordingKeyUnavailableError("Recording key storage is unavailable.")

    async def exists(self, reference: RecordingKeyReference) -> bool:
        del reference
        raise RecordingKeyUnavailableError("Recording key storage is unavailable.")
