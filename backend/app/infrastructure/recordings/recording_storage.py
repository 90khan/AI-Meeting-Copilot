"""Filesystem-private encrypted recording storage."""

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingSegmentDescriptor,
)
from app.application.exceptions import (
    RecordingSegmentAuthenticationError,
    RecordingSegmentCorruptError,
    RecordingSegmentLifecycleError,
    RecordingSegmentNotFoundError,
    RecordingStorageUnavailableError,
)
from app.application.interfaces import RecordingKeyStore
from app.infrastructure.recordings.encrypted_segment_writer import (
    _LENGTH,
    _PREFIX,
    ALGORITHM_AES_256_GCM,
    MAGIC,
    VERSION,
    EncryptedRecordingSegmentWriter,
    _aad,
)


class EncryptedRecordingStorage:
    """Map opaque tokens to encrypted, deterministic segment files."""

    def __init__(
        self,
        root: Path,
        key_store: RecordingKeyStore,
        *,
        maximum_plaintext_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if maximum_plaintext_bytes <= 0:
            raise ValueError("maximum_plaintext_bytes must be positive")
        self._root = root
        self._key_store = key_store
        self._maximum_plaintext_bytes = maximum_plaintext_bytes
        self._tokens: dict[UUID, str] = {}

    def configure_recording(
        self, recording_id: UUID, storage_directory_token: str
    ) -> None:
        if (
            not storage_directory_token
            or "/" in storage_directory_token
            or "\\" in storage_directory_token
            or ".." in storage_directory_token
        ):
            raise RecordingStorageUnavailableError()
        self._tokens[recording_id] = storage_directory_token

    async def create_segment_writer(
        self,
        recording_id: UUID,
        segment_index: int,
        key_reference: RecordingKeyReference,
    ) -> EncryptedRecordingSegmentWriter:
        if segment_index < 0:
            raise RecordingSegmentLifecycleError()
        final_path = self._directory(recording_id) / f"segment-{segment_index:06d}.amcr"
        return EncryptedRecordingSegmentWriter(
            recording_id=recording_id,
            segment_index=segment_index,
            key_reference=key_reference,
            key_store=self._key_store,
            final_path=final_path,
            maximum_plaintext_bytes=self._maximum_plaintext_bytes,
        )

    async def list_segments(
        self, recording_id: UUID
    ) -> tuple[RecordingSegmentDescriptor, ...]:
        directory = self._directory(recording_id)
        if not directory.exists():
            return ()
        descriptors = []
        for path in sorted(directory.glob("segment-*.amcr")):
            index, ciphertext_length = self._header(path.read_bytes())
            descriptors.append(
                RecordingSegmentDescriptor(
                    recording_id=recording_id,
                    segment_index=index,
                    plaintext_length=ciphertext_length - 16,
                    ciphertext_length=ciphertext_length,
                    created_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                    completed=True,
                    format_version=VERSION,
                )
            )
        return tuple(descriptors)

    async def read_segment(
        self,
        recording_id: UUID,
        segment_index: int,
        key_reference: RecordingKeyReference,
    ) -> bytes:
        if segment_index < 0:
            raise RecordingSegmentNotFoundError()
        path = self._directory(recording_id) / f"segment-{segment_index:06d}.amcr"
        if not path.is_file():
            raise RecordingSegmentNotFoundError()
        index, _, nonce, ciphertext = self._decode(path.read_bytes())
        if index != segment_index:
            raise RecordingSegmentCorruptError()
        key = await self._key_store.get_key(key_reference)
        try:
            return AESGCM(key._material()).decrypt(
                nonce, ciphertext, _aad(recording_id, index)
            )
        except InvalidTag as error:
            raise RecordingSegmentAuthenticationError() from error

    async def delete_recording(self, recording_id: UUID) -> None:
        shutil.rmtree(self._directory(recording_id), ignore_errors=True)

    async def recording_exists(self, recording_id: UUID) -> bool:
        return self._directory(recording_id).is_dir()

    def _directory(self, recording_id: UUID) -> Path:
        try:
            return self._root / self._tokens[recording_id]
        except KeyError as error:
            raise RecordingStorageUnavailableError() from error

    def _header(self, data: bytes) -> tuple[int, int]:
        index, length, _, _ = self._decode(data)
        return index, length

    def _decode(self, data: bytes) -> tuple[int, int, bytes, bytes]:
        if len(data) < _PREFIX.size + _LENGTH.size:
            raise RecordingSegmentCorruptError()
        magic, version, algorithm, index, nonce_length = _PREFIX.unpack_from(data)
        if (
            magic != MAGIC
            or version != VERSION
            or algorithm != ALGORITHM_AES_256_GCM
            or nonce_length != 12
        ):
            raise RecordingSegmentCorruptError()
        start = _PREFIX.size
        if len(data) < start + nonce_length + _LENGTH.size:
            raise RecordingSegmentCorruptError()
        nonce = data[start : start + nonce_length]
        length = _LENGTH.unpack_from(data, start + nonce_length)[0]
        ciphertext = data[start + nonce_length + _LENGTH.size :]
        if len(ciphertext) != length or length < 16:
            raise RecordingSegmentCorruptError()
        return index, length, nonce, ciphertext
