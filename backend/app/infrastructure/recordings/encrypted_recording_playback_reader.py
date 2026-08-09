"""Stream authenticated local recording segments for trusted desktop playback."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingMetadataRecord,
    RecordingPlaybackInfo,
    RecordingPlaybackSegment,
    RecordingState,
)
from app.application.exceptions import (
    RecordingKeyStoreError,
    RecordingPlaybackUnavailableError,
    RecordingStorageError,
)
from app.application.interfaces import RecordingKeyStore, RecordingStorage
from app.domain.value_objects import MeetingId
from app.infrastructure.recordings.wav_recording_segment import (
    validate_wav_recording_segment,
)


class EncryptedRecordingPlaybackReader:
    """Resolve eligible metadata and decrypt at most one segment at a time.

    Capture gaps remain represented by metadata; this reader preserves the actual
    stored segment ordering and never synthesizes audio for missing indexes.
    """

    def __init__(
        self,
        *,
        metadata_resolver: Callable[[MeetingId], Awaitable[RecordingMetadataRecord]],
        recording_storage: RecordingStorage,
        recording_key_store: RecordingKeyStore,
    ) -> None:
        self._metadata_resolver = metadata_resolver
        self._recording_storage = recording_storage
        self._recording_key_store = recording_key_store

    async def get_info(self, meeting_id: MeetingId) -> RecordingPlaybackInfo:
        """Return safe playback metadata only for a completed reviewable recording."""

        record = await self._resolve_record(meeting_id)
        metadata = record.metadata
        if not metadata.container_format.is_playback_supported:
            raise RecordingPlaybackUnavailableError()
        return RecordingPlaybackInfo(
            recording_id=metadata.recording_id,
            meeting_id=metadata.meeting_id,
            format=metadata.container_format,
            duration_seconds=metadata.duration_seconds,
            segment_count=metadata.segment_count,
            has_gaps=metadata.has_gaps,
        )

    async def read_segments(
        self,
        meeting_id: MeetingId,
    ) -> AsyncIterator[RecordingPlaybackSegment]:
        """Decrypt and yield stored segments in ascending index order."""

        record = await self._resolve_record(meeting_id)
        reference = RecordingKeyReference(value=record.key_reference)
        if not record.metadata.container_format.is_playback_supported:
            raise RecordingPlaybackUnavailableError()
        try:
            await self._recording_key_store.get_key(reference)
            descriptors = await self._recording_storage.list_segments(
                record.metadata.recording_id
            )
        except asyncio.CancelledError:
            raise
        except (RecordingKeyStoreError, RecordingStorageError) as error:
            raise RecordingPlaybackUnavailableError() from error

        ordered = tuple(sorted(descriptors, key=lambda item: item.segment_index))
        if len({item.segment_index for item in ordered}) != len(ordered):
            raise RecordingPlaybackUnavailableError()

        for descriptor in ordered:
            try:
                plaintext = await self._recording_storage.read_segment(
                    record.metadata.recording_id,
                    descriptor.segment_index,
                    reference,
                )
                validate_wav_recording_segment(plaintext)
            except asyncio.CancelledError:
                raise
            except (RecordingKeyStoreError, RecordingStorageError) as error:
                raise RecordingPlaybackUnavailableError() from error
            yield RecordingPlaybackSegment(
                segment_index=descriptor.segment_index,
                plaintext_audio=plaintext,
            )

    async def _resolve_record(self, meeting_id: MeetingId) -> RecordingMetadataRecord:
        try:
            record = await self._metadata_resolver(meeting_id)
        except asyncio.CancelledError:
            raise
        except (RecordingKeyStoreError, RecordingStorageError) as error:
            raise RecordingPlaybackUnavailableError() from error
        metadata = record.metadata
        if (
            metadata.meeting_id != meeting_id
            or metadata.state is not RecordingState.COMPLETED
            or metadata.deletion_status.value == "deleted"
            or not metadata.consent_confirmed
            or not metadata.container_format.is_playback_supported
        ):
            raise RecordingPlaybackUnavailableError()
        return record
