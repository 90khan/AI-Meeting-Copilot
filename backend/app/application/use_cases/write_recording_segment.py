"""Persist one validated WAV recording segment without retaining a database lock."""

from dataclasses import replace

from app.application.dto.recordings import (
    RecordingKeyReference,
    RecordingMediaFormat,
    RecordingSegmentDescriptor,
    RecordingSegmentTiming,
    RecordingState,
    WriteRecordingSegmentCommand,
    WriteRecordingSegmentResult,
)
from app.application.exceptions import RecordingSegmentLifecycleError
from app.application.interfaces import RecordingStorage, UnitOfWorkFactory
from app.domain.exceptions import InvalidStateTransitionError
from app.infrastructure.recordings import validate_wav_recording_segment


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

        sample_count = validate_wav_recording_segment(command.wav_bytes)

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
            previous_timing = await unit_of_work.recording_segment_timings.get_last(
                command.recording_id
            )
            expected_timing_index = self._expected_next_timing_index(previous_timing)
            if (
                command.segment_index != expected_timing_index
                or expected_metadata_index != expected_timing_index
            ):
                raise RecordingSegmentLifecycleError()
            start_sample = self._start_sample(previous_timing)

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

        try:
            async with self._unit_of_work_factory() as unit_of_work:
                current = await unit_of_work.recordings.get_by_id(command.recording_id)
                if current is None:
                    raise LookupError("Recording not found")
                self._validate_recording(
                    current.metadata.state,
                    current.metadata.container_format,
                )
                current_last_timing = (
                    await unit_of_work.recording_segment_timings.get_last(
                        command.recording_id
                    )
                )
                if (
                    current.current_segment_index != command.segment_index
                    or self._expected_next_timing_index(current_last_timing)
                    != command.segment_index
                ):
                    raise RecordingSegmentLifecycleError()
                await unit_of_work.recordings.save(
                    replace(current, current_segment_index=command.segment_index + 1)
                )
                await unit_of_work.recording_segment_timings.save(
                    RecordingSegmentTiming(
                        recording_id=command.recording_id,
                        segment_index=command.segment_index,
                        sample_count=sample_count,
                        start_sample=start_sample,
                    )
                )
                await unit_of_work.commit()
        except BaseException:
            await self._recording_storage.delete_segment(
                command.recording_id,
                command.segment_index,
            )
            raise

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

    @staticmethod
    def _expected_next_timing_index(previous: RecordingSegmentTiming | None) -> int:
        return 0 if previous is None else previous.segment_index + 1

    @staticmethod
    def _start_sample(previous: RecordingSegmentTiming | None) -> int:
        return 0 if previous is None else previous.start_sample + previous.sample_count
