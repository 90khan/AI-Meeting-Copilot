"""Private AES-256-GCM writer for one bounded recording segment."""

import os
import secrets
import struct
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingSegmentDescriptor,
)
from app.application.exceptions import (
    RecordingSegmentCorruptError,
    RecordingSegmentLifecycleError,
    RecordingSegmentTooLargeError,
)
from app.application.interfaces import RecordingKeyStore

MAGIC = b"AMCR"
VERSION = 1
ALGORITHM_AES_256_GCM = 1
_PREFIX = struct.Struct(">4sBBIB")
_LENGTH = struct.Struct(">Q")


def _aad(recording_id: UUID, segment_index: int) -> bytes:
    return recording_id.bytes + struct.pack(">IB", segment_index, VERSION)


class EncryptedRecordingSegmentWriter:
    """Buffer one bounded plaintext segment and atomically publish ciphertext."""

    def __init__(
        self,
        *,
        recording_id: UUID,
        segment_index: int,
        key_reference: RecordingKeyReference,
        key_store: RecordingKeyStore,
        final_path: Path,
        maximum_plaintext_bytes: int,
    ) -> None:
        self._recording_id = recording_id
        self._segment_index = segment_index
        self._key_reference = key_reference
        self._key_store = key_store
        self._final_path = final_path
        self._partial_path = final_path.with_suffix(".partial")
        self._maximum_plaintext_bytes = maximum_plaintext_bytes
        self._parts: list[bytes] = []
        self._length = 0
        self._finished = False

    async def write(self, data: bytes) -> None:
        if self._finished or not data:
            if self._finished:
                raise RecordingSegmentLifecycleError()
            return
        if self._length + len(data) > self._maximum_plaintext_bytes:
            raise RecordingSegmentTooLargeError()
        self._parts.append(bytes(data))
        self._length += len(data)

    async def finalize(self) -> RecordingSegmentDescriptor:
        if self._finished:
            raise RecordingSegmentLifecycleError()
        self._final_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        nonce = secrets.token_bytes(12)
        key = await self._key_store.get_key(self._key_reference)
        ciphertext = AESGCM(key._material()).encrypt(
            nonce,
            b"".join(self._parts),
            _aad(self._recording_id, self._segment_index),
        )
        payload = (
            _PREFIX.pack(
                MAGIC,
                VERSION,
                ALGORITHM_AES_256_GCM,
                self._segment_index,
                len(nonce),
            )
            + nonce
            + _LENGTH.pack(len(ciphertext))
            + ciphertext
        )
        try:
            with self._partial_path.open("xb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            self._partial_path.chmod(0o600)
            os.replace(self._partial_path, self._final_path)
        except OSError as error:
            self._partial_path.unlink(missing_ok=True)
            raise RecordingSegmentCorruptError() from error
        self._parts.clear()
        self._finished = True
        return RecordingSegmentDescriptor(
            recording_id=self._recording_id,
            segment_index=self._segment_index,
            plaintext_length=self._length,
            ciphertext_length=len(ciphertext),
            created_at=datetime.now(UTC),
            completed=True,
            format_version=VERSION,
        )

    async def abort(self) -> None:
        if not self._finished:
            self._partial_path.unlink(missing_ok=True)
            self._parts.clear()
            self._finished = True

    def __del__(self) -> None:
        if not self._finished:
            self._partial_path.unlink(missing_ok=True)
