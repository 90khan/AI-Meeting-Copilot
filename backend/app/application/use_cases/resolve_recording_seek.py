"""Resolve playback seconds to durable V1 recording segment timing metadata."""

from math import floor
from uuid import UUID

from app.application.dto.recordings import (
    RecordingDeletionStatus,
    RecordingMediaFormat,
    RecordingSeekResolution,
    RecordingSeekTarget,
    RecordingSegmentTiming,
    RecordingState,
    ResolveRecordingSeekCommand,
)
from app.application.exceptions import (
    ApplicationValidationError,
    RecordingPlaybackUnavailableError,
)
from app.application.interfaces import UnitOfWorkFactory
from app.application.services.recording_timing import (
    RECORDING_PLAYBACK_SAMPLE_RATE_HZ,
    total_samples_for_contiguous_timing,
)


class ResolveRecordingSeekUseCase:
    """Map a trusted playback target to one durable V1 segment and sample offset."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        command: ResolveRecordingSeekCommand,
    ) -> RecordingSeekResolution:
        """Resolve target seconds without touching encrypted storage or audio bytes."""

        async with self._unit_of_work_factory() as unit_of_work:
            record = await unit_of_work.recordings.get_by_meeting_id(command.meeting_id)
            if record is None:
                raise RecordingPlaybackUnavailableError()
            metadata = record.metadata
            if (
                metadata.state is not RecordingState.COMPLETED
                or metadata.deletion_status is RecordingDeletionStatus.DELETED
                or not metadata.consent_confirmed
                or metadata.container_format
                is not RecordingMediaFormat.WAV_PCM16_MONO_16KHZ_SEGMENTED_V1
            ):
                raise RecordingPlaybackUnavailableError()

            timings = await unit_of_work.recording_segment_timings.list_for_recording(
                metadata.recording_id
            )
            total_samples = total_samples_for_contiguous_timing(
                recording_id=metadata.recording_id,
                timings=timings,
            )
            authoritative_duration = total_samples / RECORDING_PLAYBACK_SAMPLE_RATE_HZ
            if command.target_seconds > authoritative_duration:
                raise ApplicationValidationError("Recording seek target is invalid.")
            if command.target_seconds == authoritative_duration:
                return RecordingSeekResolution(target=None, at_end=True)

            target_sample = floor(
                command.target_seconds * RECORDING_PLAYBACK_SAMPLE_RATE_HZ
            )
            target = self._find_target(
                recording_id=metadata.recording_id,
                timings=timings,
                target_sample=target_sample,
            )
            return RecordingSeekResolution(target=target, at_end=False)

    @staticmethod
    def _find_target(
        *,
        recording_id: UUID,
        timings: tuple[RecordingSegmentTiming, ...],
        target_sample: int,
    ) -> RecordingSeekTarget:
        """Find the containing timing row with deterministic binary search."""

        low = 0
        high = len(timings) - 1
        while low <= high:
            middle = (low + high) // 2
            timing = timings[middle]
            segment_end = timing.start_sample + timing.sample_count
            if target_sample < timing.start_sample:
                high = middle - 1
            elif target_sample >= segment_end:
                low = middle + 1
            else:
                offset_samples = target_sample - timing.start_sample
                return RecordingSeekTarget(
                    recording_id=recording_id,
                    segment_index=timing.segment_index,
                    segment_start_sample=timing.start_sample,
                    segment_sample_count=timing.sample_count,
                    target_sample=target_sample,
                    offset_samples=offset_samples,
                    resolved_seconds=(
                        target_sample / RECORDING_PLAYBACK_SAMPLE_RATE_HZ
                    ),
                )
        raise RecordingPlaybackUnavailableError()
