"""Tests for Meeting history query orchestration."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import MeetingHistoryItem
from app.application.dto.recordings import RecordingState
from app.application.exceptions import ApplicationValidationError
from app.application.use_cases import ListMeetingsUseCase
from app.domain.value_objects import MeetingId, MeetingStatus


def _item(value: int, *, state: RecordingState | None = None) -> MeetingHistoryItem:
    return MeetingHistoryItem(
        meeting_id=MeetingId(UUID(int=value)),
        title=f"Meeting {value}",
        status=MeetingStatus.ENDED,
        created_at=datetime(2026, 1, value, tzinfo=UTC),
        started_at=None,
        ended_at=None,
        transcript_count=value,
        recording_available=state is RecordingState.COMPLETED,
        recording_state=state,
        audio_expires_at=None,
        audio_protected=False,
    )


class _Reviews:
    def __init__(self, items: tuple[MeetingHistoryItem, ...]) -> None:
        self.items = items
        self.arguments: tuple[int, int] | None = None

    async def list_meetings(
        self, *, limit: int, offset: int
    ) -> tuple[MeetingHistoryItem, ...]:
        self.arguments = (limit, offset)
        return self.items[offset : offset + limit]


class _UnitOfWork:
    def __init__(self, reviews: _Reviews) -> None:
        self.meeting_reviews = reviews

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def test_list_honors_pagination_and_does_not_commit() -> None:
    reviews = _Reviews((_item(3), _item(2), _item(1)))
    result = asyncio.run(
        ListMeetingsUseCase(lambda: _UnitOfWork(reviews)).execute(limit=2, offset=1)
    )
    assert [item.meeting_id.value.int for item in result] == [2, 1]
    assert reviews.arguments == (2, 1)


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (501, 0), (1, -1)])
def test_invalid_pagination_is_rejected(limit: int, offset: int) -> None:
    with pytest.raises(ApplicationValidationError):
        asyncio.run(
            ListMeetingsUseCase(lambda: _UnitOfWork(_Reviews(()))).execute(
                limit=limit, offset=offset
            )
        )


def test_recording_availability_policy_is_exposed_by_read_models() -> None:
    assert _item(1, state=RecordingState.COMPLETED).recording_available is True
    for state in (
        RecordingState.DELETED,
        RecordingState.MISSING,
        RecordingState.FAILED,
        RecordingState.PENDING,
        RecordingState.RECORDING,
        RecordingState.FINALIZING,
    ):
        assert _item(1, state=state).recording_available is False
