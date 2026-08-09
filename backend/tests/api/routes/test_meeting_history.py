"""Tests for privacy-safe Meeting history HTTP serialization."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.api.routes.meeting_history import router
from app.application.dto import AudioSource
from app.application.dto.meeting_review import (
    MeetingDetail,
    MeetingHistoryItem,
    TranscriptReadItem,
)
from app.domain.value_objects import MeetingId, MeetingStatus
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _history() -> MeetingHistoryItem:
    return MeetingHistoryItem(
        meeting_id=MeetingId(UUID(int=1)),
        title="Meeting",
        status=MeetingStatus.ENDED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=None,
        ended_at=None,
        transcript_count=1,
        recording_available=False,
        recording_state=None,
        audio_expires_at=None,
        audio_protected=False,
    )


def _detail() -> MeetingDetail:
    return MeetingDetail(
        meeting_id=MeetingId(UUID(int=1)),
        title="Meeting",
        status=MeetingStatus.ENDED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=None,
        ended_at=None,
        transcript=(
            TranscriptReadItem(
                transcript_id=UUID(int=2),
                text="exact persisted text",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                speaker="Unknown",
                source=AudioSource.MIXED,
            ),
        ),
        recording_available=False,
        recording_state=None,
        recording_duration_seconds=None,
        audio_expires_at=None,
        audio_protected=False,
        audio_has_gaps=False,
    )


def _client(*, missing: bool = False) -> TestClient:
    class _List:
        async def execute(self, **_: object):
            return (_history(),)

    class _Detail:
        async def execute(self, **_: object):
            if missing:
                raise LookupError("Meeting not found")
            return _detail()

    app = FastAPI()
    app.state.container = SimpleNamespace(
        get_list_meetings_use_case=lambda: _List(),
        get_get_meeting_detail_use_case=lambda: _Detail(),
    )
    app.include_router(router)
    return TestClient(app)


def test_list_and_detail_are_safe_and_preserve_transcript() -> None:
    client = _client()
    listing = client.get("/api/v1/meetings")
    detail = client.get(f"/api/v1/meetings/{UUID(int=1)}")
    assert listing.status_code == detail.status_code == 200
    assert listing.json()["meetings"][0]["meeting_id"] == str(UUID(int=1))
    assert detail.json()["transcript"][0]["text"] == "exact persisted text"
    assert detail.json()["transcript"][0]["timestamp"].endswith("+00:00")
    assert not {"key_reference", "storage_directory_token", "failure_code"} & set(
        detail.json()
    )


def test_missing_and_malformed_ids_are_safe_4xx() -> None:
    assert (
        _client(missing=True).get(f"/api/v1/meetings/{UUID(int=1)}").status_code == 404
    )
    assert _client().get("/api/v1/meetings/not-a-uuid").status_code == 422
