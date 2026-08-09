"""Tests for streaming, authenticated local recording playback reads."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never
from uuid import UUID

import pytest
from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingEncryptionKey,
    RecordingKeyReference,
    RecordingMetadata,
    RecordingMetadataRecord,
    RecordingRetentionPolicy,
    RecordingSegmentDescriptor,
    RecordingState,
)
from app.application.exceptions import RecordingPlaybackUnavailableError
from app.domain.value_objects import MeetingId
from app.infrastructure.recordings import (
    EncryptedRecordingPlaybackReader,
    EncryptedRecordingStorage,
)

_MEETING_ID = MeetingId(UUID(int=1))
_RECORDING_ID = UUID(int=2)
_REFERENCE = RecordingKeyReference(value="a" * 24)


class _Keys:
    def __init__(self, key: bytes = b"k" * 32) -> None:
        self.key = key

    async def get_key(self, reference: RecordingKeyReference) -> RecordingEncryptionKey:
        return RecordingEncryptionKey(_value=self.key)


def _record(**overrides: object) -> RecordingMetadataRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    metadata = RecordingMetadata(
        recording_id=_RECORDING_ID,
        meeting_id=_MEETING_ID,
        state=RecordingState.COMPLETED,
        retention_policy=RecordingRetentionPolicy.SEVEN_DAYS,
        created_at=now,
        expires_at=now + timedelta(days=7),
        capture_anchor_utc=now,
        duration_seconds=2.0,
        protected=False,
        consent_confirmed=True,
        consent_confirmed_at=now,
        deletion_status=RecordingDeletionStatus.SCHEDULED,
        deleted_at=None,
        encryption_format_version=1,
        container_format="m4a",
        segment_count=2,
        has_gaps=True,
    )
    values: dict[str, object] = {
        "metadata": metadata,
        "storage_directory_token": "opaque-token",
        "key_reference": _REFERENCE.value,
        "current_segment_index": 2,
    }
    values.update(overrides)
    return RecordingMetadataRecord(**values)  # type: ignore[arg-type]


async def _reader(
    storage: EncryptedRecordingStorage,
    keys: _Keys,
    record: RecordingMetadataRecord,
) -> EncryptedRecordingPlaybackReader:
    async def resolve(meeting_id: MeetingId) -> RecordingMetadataRecord:
        if meeting_id != _MEETING_ID:
            raise RecordingPlaybackUnavailableError()
        return record

    return EncryptedRecordingPlaybackReader(
        metadata_resolver=resolve,
        recording_storage=storage,
        recording_key_store=keys,
    )


async def _write(
    storage: EncryptedRecordingStorage,
    index: int,
    payload: bytes,
) -> None:
    writer = await storage.create_segment_writer(_RECORDING_ID, index, _REFERENCE)
    await writer.write(payload)
    await writer.finalize()


def _storage(tmp_path: Path, keys: _Keys) -> EncryptedRecordingStorage:
    storage = EncryptedRecordingStorage(tmp_path, keys)
    storage.configure_recording(_RECORDING_ID, "opaque-token")
    return storage


def test_completed_info_and_segments_stream_in_ascending_order(tmp_path: Path) -> None:
    keys = _Keys()
    storage = _storage(tmp_path, keys)

    async def exercise() -> tuple[object, list[tuple[int, bytes]]]:
        await _write(storage, 2, b"two")
        await _write(storage, 0, b"zero")
        reader = await _reader(storage, keys, _record())
        info = await reader.get_info(_MEETING_ID)
        segments = [
            (item.segment_index, item.plaintext_audio)
            async for item in reader.read_segments(_MEETING_ID)
        ]
        return info, segments

    info, segments = asyncio.run(exercise())
    assert info.has_gaps is True
    assert segments == [(0, b"zero"), (2, b"two")]
    assert not list(tmp_path.rglob("*.m4a"))


def test_playback_storage_preserves_opaque_plaintext_without_media_validation(
    tmp_path: Path,
) -> None:
    """The current storage boundary has no codec or container contract.

    This deliberately non-media payload proves that the encryption/playback
    path preserves arbitrary bytes.  It must not be treated as an M4A, WAV,
    raw AAC, PCM, or fragmented-MP4 producer until an encoder establishes one.
    """

    keys = _Keys()
    storage = _storage(tmp_path, keys)
    opaque_payload = b"not-a-media-container\x00\xff"

    async def exercise() -> bytes:
        await _write(storage, 0, opaque_payload)
        reader = await _reader(storage, keys, _record())
        async for item in reader.read_segments(_MEETING_ID):
            return item.plaintext_audio
        raise AssertionError("expected one opaque playback segment")

    assert asyncio.run(exercise()) == opaque_payload
    assert not list(tmp_path.rglob("*.m4a"))
    assert not list(tmp_path.rglob("*.wav"))


@pytest.mark.parametrize(
    "state",
    [
        RecordingState.PENDING,
        RecordingState.RECORDING,
        RecordingState.FINALIZING,
        RecordingState.FAILED,
        RecordingState.MISSING,
        RecordingState.DELETED,
    ],
)
def test_ineligible_states_are_private_unavailable(
    tmp_path: Path,
    state: RecordingState,
) -> None:
    keys = _Keys()
    storage = _storage(tmp_path, keys)
    record = _record()
    if state is RecordingState.DELETED:
        metadata = replace(
            record.metadata,
            deletion_status=RecordingDeletionStatus.DELETED,
            deleted_at=datetime(2026, 1, 2, tzinfo=UTC),
            state=state,
        )
    else:
        metadata = replace(record.metadata, state=state)
    reader = asyncio.run(_reader(storage, keys, replace(record, metadata=metadata)))
    with pytest.raises(RecordingPlaybackUnavailableError):
        asyncio.run(reader.get_info(_MEETING_ID))


def test_wrong_key_and_missing_storage_are_privacy_safe(tmp_path: Path) -> None:
    keys = _Keys()
    storage = _storage(tmp_path, keys)

    async def exercise() -> None:
        await _write(storage, 0, b"secret")
        wrong_keys = _Keys(b"z" * 32)
        wrong_storage = _storage(tmp_path, wrong_keys)
        wrong_reader = await _reader(wrong_storage, wrong_keys, _record())
        with pytest.raises(RecordingPlaybackUnavailableError) as error:
            _ = [item async for item in wrong_reader.read_segments(_MEETING_ID)]
        assert "secret" not in str(error.value)

        missing_record = _record(storage_directory_token="missing-token")
        missing = await _reader(
            EncryptedRecordingStorage(tmp_path, keys),
            keys,
            missing_record,
        )
        with pytest.raises(RecordingPlaybackUnavailableError):
            _ = [item async for item in missing.read_segments(_MEETING_ID)]

    asyncio.run(exercise())


def test_duplicate_descriptors_and_cancellation_do_not_return_audio() -> None:
    descriptor = RecordingSegmentDescriptor(
        recording_id=_RECORDING_ID,
        segment_index=0,
        plaintext_length=1,
        ciphertext_length=17,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed=True,
        format_version=1,
    )

    class DuplicateStorage:
        async def create_segment_writer(
            self,
            recording_id: UUID,
            segment_index: int,
            key_reference: RecordingKeyReference,
        ) -> Never:
            raise AssertionError("writer is not used for playback")

        async def list_segments(self, recording_id: UUID):
            return (descriptor, descriptor)

        async def read_segment(
            self,
            recording_id: UUID,
            segment_index: int,
            key_reference: RecordingKeyReference,
        ) -> bytes:
            raise AssertionError("duplicate descriptors must fail before decryption")

        async def delete_recording(self, recording_id: UUID) -> None:
            raise AssertionError("deletion is not used for playback")

        async def recording_exists(self, recording_id: UUID) -> bool:
            return True

    async def resolve(meeting_id: MeetingId) -> RecordingMetadataRecord:
        return _record()

    async def exercise() -> None:
        reader = EncryptedRecordingPlaybackReader(
            metadata_resolver=resolve,
            recording_storage=DuplicateStorage(),
            recording_key_store=_Keys(),
        )
        with pytest.raises(RecordingPlaybackUnavailableError):
            _ = [item async for item in reader.read_segments(_MEETING_ID)]

        async def cancelled_resolve(meeting_id: MeetingId) -> RecordingMetadataRecord:
            raise asyncio.CancelledError()

        cancelled = EncryptedRecordingPlaybackReader(
            metadata_resolver=cancelled_resolve,
            recording_storage=DuplicateStorage(),
            recording_key_store=_Keys(),
        )
        with pytest.raises(asyncio.CancelledError):
            await cancelled.get_info(_MEETING_ID)

    asyncio.run(exercise())
