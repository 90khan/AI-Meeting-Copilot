from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from app.application.dto.recordings import RecordingSegmentTiming
from app.application.exceptions import ApplicationValidationError


def test_timing_is_valid_immutable_and_private() -> None:
    timing = RecordingSegmentTiming(
        recording_id=UUID(int=1), segment_index=0, sample_count=80_000, start_sample=0
    )
    assert timing.sample_count == 80_000
    assert not {"audio", "key", "path", "token"} & set(timing.__dataclass_fields__)
    with pytest.raises(FrozenInstanceError):
        timing.sample_count = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "values",
    [
        {
            "recording_id": "id",
            "segment_index": 0,
            "sample_count": 1,
            "start_sample": 0,
        },
        {
            "recording_id": UUID(int=1),
            "segment_index": -1,
            "sample_count": 1,
            "start_sample": 0,
        },
        {
            "recording_id": UUID(int=1),
            "segment_index": 0,
            "sample_count": 0,
            "start_sample": 0,
        },
        {
            "recording_id": UUID(int=1),
            "segment_index": 0,
            "sample_count": -1,
            "start_sample": 0,
        },
        {
            "recording_id": UUID(int=1),
            "segment_index": 0,
            "sample_count": 1,
            "start_sample": -1,
        },
    ],
)
def test_timing_rejects_invalid_values(values: dict[str, object]) -> None:
    with pytest.raises(ApplicationValidationError):
        RecordingSegmentTiming(**values)  # type: ignore[arg-type]
