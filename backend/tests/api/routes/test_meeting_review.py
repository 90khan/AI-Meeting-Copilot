"""Tests for safe completed Meeting review HTTP routes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.api.routes import meeting_review
from app.application.dto.meeting_review import (
    GenerateMeetingReviewResult,
    GetMeetingReviewQuery,
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
)
from app.application.exceptions import ProviderError
from app.domain.value_objects import MeetingId
from fastapi import FastAPI
from fastapi.testclient import TestClient

_MEETING_ID = MeetingId(UUID(int=1))
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _artifact() -> MeetingReviewArtifact:
    return MeetingReviewArtifact(
        artifact_id=UUID(int=2),
        meeting_id=_MEETING_ID,
        version=1,
        review_type="interview_review",
        status=MeetingReviewArtifactStatus.COMPLETED,
        created_at=_NOW,
        completed_at=_NOW,
        source_transcript_count=0,
        content=MeetingReviewContent(
            summary="Review summary.",
            key_decisions=("Decision.",),
            action_items=(),
            open_questions=(),
            technical_questions=(),
            technical_terms=(),
            feedback=None,
        ),
        provider_name="ollama",
        model_name="model",
        prompt_version="private",
        schema_version=1,
        failure_code=None,
    )


class _Generate:
    def __init__(
        self,
        artifact: MeetingReviewArtifact,
        error: ProviderError | None = None,
    ) -> None:
        self.artifact = artifact
        self.error = error

    async def execute(self, command: object) -> GenerateMeetingReviewResult:
        if self.error is not None:
            raise self.error
        return GenerateMeetingReviewResult(
            artifact=self.artifact,
            reused_existing=False,
        )


class _Read:
    def __init__(self, artifact: MeetingReviewArtifact | None) -> None:
        self.artifact = artifact

    async def execute(self, query: GetMeetingReviewQuery) -> object:
        if self.artifact is None:
            raise LookupError("Meeting review not found")
        return SimpleNamespace(artifact=self.artifact)


def _client(
    artifact: MeetingReviewArtifact | None,
    error: ProviderError | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(meeting_review.router)
    app.state.container = SimpleNamespace(
        get_generate_meeting_review_use_case=lambda: _Generate(_artifact(), error),
        get_get_meeting_review_use_case=lambda: _Read(artifact),
    )
    return TestClient(app)


def test_post_and_get_return_only_safe_completed_review_content() -> None:
    artifact = _artifact()
    client = _client(artifact)

    post = client.post(f"/api/v1/meetings/{_MEETING_ID}/review", json={})
    get = client.get(f"/api/v1/meetings/{_MEETING_ID}/review?version=1")

    assert post.status_code == 200
    assert get.status_code == 200
    assert post.json()["reused_existing"] is False
    assert get.json()["created_at"].endswith("+00:00")
    assert get.json()["content"]["key_decisions"] == ["Decision."]
    private_fields = {
        "provider_name",
        "model_name",
        "prompt_version",
        "schema_version",
        "failure_code",
    }
    assert private_fields.isdisjoint(get.json())


def test_provider_failure_and_invalid_or_missing_requests_are_safe() -> None:
    unavailable = _client(None, ProviderError("private output")).post(
        f"/api/v1/meetings/{_MEETING_ID}/review", json={}
    )
    missing = _client(None).get(f"/api/v1/meetings/{_MEETING_ID}/review")
    malformed = _client(None).get("/api/v1/meetings/not-a-uuid/review")
    invalid_version = _client(None).get(
        f"/api/v1/meetings/{_MEETING_ID}/review?version=0"
    )

    assert unavailable.status_code == 503
    assert "private" not in unavailable.text
    assert missing.status_code == 404
    assert malformed.status_code == 422
    assert invalid_version.status_code == 422
