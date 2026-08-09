"""Tests for complete persisted Meeting transcript reads."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.application.dto import AudioSource
from app.application.dto.meeting_review import MeetingDetail, TranscriptReadItem
from app.application.use_cases import GetMeetingDetailUseCase
from app.domain.value_objects import MeetingId, MeetingStatus


def _detail(count: int) -> MeetingDetail:
    transcript = tuple(
        TranscriptReadItem(
            transcript_id=UUID(int=index + 1),
            text=f" exact {index} ",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            speaker="Unknown",
            source=AudioSource.MIXED,
        )
        for index in range(count)
    )
    return MeetingDetail(
        meeting_id=MeetingId(UUID(int=1)),
        title="Review",
        status=MeetingStatus.ENDED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=None,
        ended_at=None,
        transcript=transcript,
        recording_available=False,
        recording_state=None,
        recording_duration_seconds=None,
        audio_expires_at=None,
        audio_protected=False,
        audio_has_gaps=False,
    )


class _Reviews:
    def __init__(self, detail: MeetingDetail | None) -> None:
        self.detail = detail

    async def get_meeting_detail(self, meeting_id: MeetingId) -> MeetingDetail | None:
        return self.detail


class _UnitOfWork:
    def __init__(self, reviews: _Reviews) -> None:
        self.meeting_reviews = reviews

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def test_missing_meeting_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="Meeting not found"):
        asyncio.run(
            GetMeetingDetailUseCase(lambda: _UnitOfWork(_Reviews(None))).execute(
                meeting_id=MeetingId.new()
            )
        )


def test_large_transcript_is_not_truncated_or_changed() -> None:
    detail = _detail(2_001)
    result = asyncio.run(
        GetMeetingDetailUseCase(lambda: _UnitOfWork(_Reviews(detail))).execute(
            meeting_id=detail.meeting_id
        )
    )
    assert len(result.transcript) == 2_001
    assert result.transcript[0].text == " exact 0 "
    assert result.transcript[-1].text == " exact 2000 "
    assert all(item.source is AudioSource.MIXED for item in result.transcript)
