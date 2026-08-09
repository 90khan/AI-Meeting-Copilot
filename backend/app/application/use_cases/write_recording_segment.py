"""Persist one validated WAV recording segment without retaining a database lock."""

from dataclasses import replace

from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingMediaFormat,
    RecordingSegmentDescriptor,
    RecordingState,
    WriteRecordingSegmentCommand,
    WriteRecordingSegmentResult,
)
from app.application.exceptions import RecordingSegmentLifecycleError
from app.application.interfaces import RecordingStorage, UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError


class WriteRecordingSegmentUseCase:
    """Write one sequential encrypted segment through the recording storage boundary."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        recording_storage: RecordingStorage,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._recording_storage = recording_storage

    async def execute(
        self, command: WriteRecordingSegmentCommand
    ) -> WriteRecordingSegmentResult:
        """Persist one segment in short database phases around external I/O."""

        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_id(command.recording_id)
            if record is None:
                raise LookupError("Recording not found")
            self._validate_recording(
                record.metadata.state,
                record.metadata.container_format,
            )
            key_reference = RecordingKeyReference(value=record.key_reference)
            expected_metadata_index = record.current_segment_index

        segments = await self._recording_storage.list_segments(command.recording_id)
        expected_index = self._expected_next_index(segments)
        if (
            command.segment_index != expected_index
            or expected_metadata_index != expected_index
        ):
            raise RecordingSegmentLifecycleError()

        writer = await self._recording_storage.create_segment_writer(
            command.recording_id,
            command.segment_index,
            key_reference,
        )
        try:
            await writer.write(command.wav_bytes)
            await writer.finalize()
        except BaseException:
            await writer.abort()
            raise

        async with self._unit_of_work_factory() as unit_of_work:
            current = await unit_of_work.recordings.get_by_id(command.recording_id)
            if current is None:
                raise LookupError("Recording not found")
            self._validate_recording(
                current.metadata.state,
                current.metadata.container_format,
            )
            if current.current_segment_index != command.segment_index:
                raise RecordingSegmentLifecycleError()
            await unit_of_work.recordings.save(
                replace(current, current_segment_index=command.segment_index + 1)
            )
            await unit_of_work.commit()

        return WriteRecordingSegmentResult(
            recording_id=command.recording_id,
            segment_index=command.segment_index,
        )

    @staticmethod
    def _validate_recording(
        state: RecordingState,
        container_format: RecordingMediaFormat,
    ) -> None:
        if state is not RecordingState.RECORDING:
            raise InvalidStateTransitionError("Recording is not active.")
        if (
            container_format
            is not RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1
        ):
            raise RecordingSegmentLifecycleError()

    @staticmethod
    def _expected_next_index(segments: tuple[RecordingSegmentDescriptor, ...]) -> int:
        indexes = tuple(segment.segment_index for segment in segments)
        if indexes != tuple(range(len(indexes))):
            raise RecordingSegmentLifecycleError()
        return len(indexes)
