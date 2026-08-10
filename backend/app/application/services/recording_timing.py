"""Pure validation for durable V1 recording playback timing."""

from uuid import UUID

from app.application.dto.recordings.segment_timing import RecordingSegmentTiming
from app.application.exceptions import ApplicationValidationError

RECORDING_PLAYBACK_SAMPLE_RATE_HZ = 16_000


def total_samples_for_contiguous_timing(
    *,
    recording_id: UUID,
    timings: tuple[RecordingSegmentTiming, ...],
) -> int:
    """Validate a V1 timing sequence and return its authoritative sample count."""

    if not timings:
        raise ApplicationValidationError("Recording timing metadata is invalid.")

    previous: RecordingSegmentTiming | None = None
    total_samples = 0
    for timing in timings:
        if timing.recording_id != recording_id or timing.sample_count <= 0:
            raise ApplicationValidationError("Recording timing metadata is invalid.")
        if previous is None:
            if timing.segment_index != 0 or timing.start_sample != 0:
                raise ApplicationValidationError(
                    "Recording timing metadata is invalid."
                )
        elif (
            timing.segment_index != previous.segment_index + 1
            or timing.start_sample != previous.start_sample + previous.sample_count
        ):
            raise ApplicationValidationError("Recording timing metadata is invalid.")
        total_samples += timing.sample_count
        previous = timing

    return total_samples
