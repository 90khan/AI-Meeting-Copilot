"""Focused AES-GCM segment storage tests using temporary directories."""

import asyncio
import os
from pathlib import Path
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingEncryptionKey,
    RecordingKeyReference,
)
from app.application.exceptions import (
    RecordingSegmentAuthenticationError,
    RecordingSegmentCorruptError,
    RecordingSegmentLifecycleError,
    RecordingSegmentNotFoundError,
    RecordingSegmentTooLargeError,
    RecordingStorageUnavailableError,
)
from app.infrastructure.recordings import EncryptedRecordingStorage
from app.infrastructure.recordings.encrypted_segment_writer import _PREFIX

REFERENCE = RecordingKeyReference(value="a" * 24)
TOKEN = "opaque-token-1234"
RECORDING_ID = UUID(int=1)


class FakeKeyStore:
    """Deterministic key-store fake that never logs key material."""

    def __init__(self, key: bytes = b"k" * 32) -> None:
        self.key = key

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        return RecordingEncryptionKey(_value=self.key)


def _storage(
    root: Path,
    *,
    recording_id: UUID = RECORDING_ID,
    key: bytes = b"k" * 32,
    maximum_plaintext_bytes: int = 64,
) -> tuple[EncryptedRecordingStorage, UUID]:
    storage = EncryptedRecordingStorage(
        root,
        FakeKeyStore(key),
        maximum_plaintext_bytes=maximum_plaintext_bytes,
    )
    storage.configure_recording(recording_id, TOKEN)
    return storage, recording_id


async def _write(
    storage: EncryptedRecordingStorage,
    recording_id: UUID,
    index: int,
    *parts: bytes,
) -> tuple[object, Path]:
    writer = await storage.create_segment_writer(recording_id, index, REFERENCE)
    for part in parts:
        await writer.write(part)
    descriptor = await writer.finalize()
    return descriptor, Path(f"segment-{index:06d}.amcr")


def test_encryption_round_trip_ciphertext_and_unique_nonces(tmp_path: Path) -> None:
    storage, recording_id = _storage(tmp_path)

    async def exercise() -> tuple[bytes, bytes]:
        await _write(storage, recording_id, 0, b"secret audio")
        await _write(storage, recording_id, 1, b"secret audio")
        assert await storage.read_segment(recording_id, 0, REFERENCE) == b"secret audio"
        return (
            (tmp_path / TOKEN / "segment-000000.amcr").read_bytes(),
            (tmp_path / TOKEN / "segment-000001.amcr").read_bytes(),
        )

    first, second = asyncio.run(exercise())
    assert b"secret audio" not in first
    assert first != second


def test_aad_and_key_authentication_fail_without_sensitive_details(
    tmp_path: Path,
) -> None:
    storage, recording_id = _storage(tmp_path)
    asyncio.run(_write(storage, recording_id, 0, b"audio"))
    path = tmp_path / TOKEN / "segment-000000.amcr"

    wrong_key_storage, _ = _storage(tmp_path, key=b"z" * 32)
    with pytest.raises(RecordingSegmentAuthenticationError) as wrong_key:
        asyncio.run(wrong_key_storage.read_segment(recording_id, 0, REFERENCE))
    assert "audio" not in str(wrong_key.value)
    assert str(REFERENCE) not in str(wrong_key.value)

    different_recording = UUID(int=2)
    aad_storage, _ = _storage(tmp_path, recording_id=different_recording)
    aad_storage.configure_recording(different_recording, TOKEN)
    with pytest.raises(RecordingSegmentAuthenticationError):
        asyncio.run(aad_storage.read_segment(different_recording, 0, REFERENCE))

    modified = bytearray(path.read_bytes())
    modified[-1] ^= 1
    path.write_bytes(modified)
    with pytest.raises(RecordingSegmentAuthenticationError):
        asyncio.run(storage.read_segment(recording_id, 0, REFERENCE))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: b"NOPE" + payload[4:],
        lambda payload: payload[:4] + b"\x02" + payload[5:],
        lambda payload: payload[:5] + b"\x02" + payload[6:],
        lambda payload: payload[:10] + b"\x08" + payload[11:],
        lambda payload: payload[:-1],
        lambda payload: payload + b"trailing",
    ],
)
def test_private_binary_parser_rejects_malformed_files(
    tmp_path: Path,
    mutation: object,
) -> None:
    storage, recording_id = _storage(tmp_path)
    asyncio.run(_write(storage, recording_id, 0, b"audio"))
    payload = (tmp_path / TOKEN / "segment-000000.amcr").read_bytes()
    assert callable(mutation)
    with pytest.raises(RecordingSegmentCorruptError):
        storage._decode(mutation(payload))  # type: ignore[operator]
    with pytest.raises(RecordingSegmentCorruptError):
        storage._decode(payload[: _PREFIX.size - 1])


def test_writer_lifecycle_size_limit_and_atomic_publication(tmp_path: Path) -> None:
    storage, recording_id = _storage(tmp_path, maximum_plaintext_bytes=5)

    async def exercise() -> tuple[object, Path]:
        writer = await storage.create_segment_writer(recording_id, 0, REFERENCE)
        await writer.write(b"ab")
        await writer.write(b"cde")
        with pytest.raises(RecordingSegmentTooLargeError):
            await writer.write(b"f")
        descriptor = await writer.finalize()
        with pytest.raises(RecordingSegmentLifecycleError):
            await writer.finalize()
        with pytest.raises(RecordingSegmentLifecycleError):
            await writer.write(b"x")
        await writer.abort()
        return descriptor, tmp_path / TOKEN / "segment-000000.amcr"

    descriptor, final_path = asyncio.run(exercise())
    assert descriptor.plaintext_length == 5
    assert final_path.is_file()
    assert not final_path.with_suffix(".partial").exists()
    assert asyncio.run(storage.read_segment(recording_id, 0, REFERENCE)) == b"abcde"
    if os.name == "posix":
        assert final_path.stat().st_mode & 0o077 == 0


def test_abort_listing_delete_and_recording_isolation(tmp_path: Path) -> None:
    storage, recording_id = _storage(tmp_path)
    second_id = UUID(int=2)
    storage.configure_recording(second_id, "another-opaque-token")

    async def exercise() -> tuple[tuple[object, ...], bool, bool]:
        writer = await storage.create_segment_writer(recording_id, 2, REFERENCE)
        await writer.write(b"partial")
        await writer.abort()
        await writer.abort()
        with pytest.raises(RecordingSegmentLifecycleError):
            await writer.write(b"later")
        await _write(storage, recording_id, 2, b"two")
        await _write(storage, recording_id, 1, b"one")
        await _write(storage, second_id, 0, b"isolated")
        descriptors = await storage.list_segments(recording_id)
        before = await storage.recording_exists(recording_id)
        await storage.delete_recording(recording_id)
        after = await storage.recording_exists(recording_id)
        await storage.delete_recording(recording_id)
        return descriptors, before, after

    descriptors, before, after = asyncio.run(exercise())
    assert [descriptor.segment_index for descriptor in descriptors] == [1, 2]
    assert [descriptor.plaintext_length for descriptor in descriptors] == [3, 3]
    assert before is True
    assert after is False
    assert (tmp_path / "another-opaque-token").is_dir()
    with pytest.raises(RecordingSegmentNotFoundError):
        asyncio.run(storage.read_segment(recording_id, 0, REFERENCE))


@pytest.mark.parametrize("token", ["../escape", "/absolute", "nested/path", "a\\b"])
def test_storage_rejects_path_traversal_and_negative_indices(
    tmp_path: Path, token: str
) -> None:
    storage, recording_id = _storage(tmp_path)
    with pytest.raises(RecordingStorageUnavailableError):
        storage.configure_recording(UUID(int=3), token)
    with pytest.raises(RecordingSegmentLifecycleError):
        asyncio.run(storage.create_segment_writer(recording_id, -1, REFERENCE))
