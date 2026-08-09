"""Tests for public completed Meeting review artifact reads."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.dto.meeting_review import (
    GetMeetingReviewQuery,
    MeetingReviewArtifact,
    MeetingReviewArtifactStatus,
    MeetingReviewContent,
)
from app.application.use_cases import GetMeetingReviewUseCase
from app.domain.value_objects import MeetingId

_MEETING_ID = MeetingId(UUID(int=1))


def _artifact(status: MeetingReviewArtifactStatus) -> MeetingReviewArtifact:
    return MeetingReviewArtifact(
        artifact_id=UUID(int=2),
        meeting_id=_MEETING_ID,
        version=3,
        review_type="interview_review",
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=(
            datetime(2026, 1, 1, tzinfo=UTC)
            if status is MeetingReviewArtifactStatus.COMPLETED
            else None
        ),
        source_transcript_count=0,
        content=(
            MeetingReviewContent(
                summary="Review.",
                key_decisions=(),
                action_items=(),
                open_questions=(),
                technical_questions=(),
                technical_terms=(),
                feedback=None,
            )
            if status is MeetingReviewArtifactStatus.COMPLETED
            else None
        ),
        provider_name="ollama",
        model_name="model",
        prompt_version="version",
        schema_version=1,
        failure_code=(
            "review_provider_failed"
            if status is MeetingReviewArtifactStatus.FAILED
            else None
        ),
    )


class _Artifacts:
    def __init__(self, artifact: MeetingReviewArtifact | None) -> None:
        self.artifact = artifact

    async def get_latest_completed(
        self, meeting_id: MeetingId, review_type: str
    ) -> MeetingReviewArtifact | None:
        return self.artifact

    async def get_by_meeting_and_version(
        self, meeting_id: MeetingId, review_type: str, version: int
    ) -> MeetingReviewArtifact | None:
        return self.artifact


class _UnitOfWork:
    def __init__(self, artifact: MeetingReviewArtifact | None) -> None:
        self.meeting_review_artifacts = _Artifacts(artifact)
        self.commit_calls = 0

    async def __aenter__(self) -> "_UnitOfWork":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def commit(self) -> None:
        self.commit_calls += 1


@pytest.mark.parametrize(
    "status",
    [
        MeetingReviewArtifactStatus.FAILED,
        MeetingReviewArtifactStatus.CANCELLED,
        MeetingReviewArtifactStatus.PROCESSING,
        MeetingReviewArtifactStatus.PENDING,
    ],
)
def test_non_completed_artifacts_are_not_public_readable(
    status: MeetingReviewArtifactStatus,
) -> None:
    unit_of_work = _UnitOfWork(_artifact(status))
    use_case = GetMeetingReviewUseCase(lambda: unit_of_work)

    with pytest.raises(LookupError, match="Meeting review not found"):
        asyncio.run(use_case.execute(GetMeetingReviewQuery(meeting_id=_MEETING_ID)))

    assert unit_of_work.commit_calls == 0


def test_latest_and_explicit_completed_artifact_are_returned_unchanged() -> None:
    artifact = _artifact(MeetingReviewArtifactStatus.COMPLETED)
    unit_of_work = _UnitOfWork(artifact)
    use_case = GetMeetingReviewUseCase(lambda: unit_of_work)

    latest = asyncio.run(
        use_case.execute(GetMeetingReviewQuery(meeting_id=_MEETING_ID))
    )
    explicit = asyncio.run(
        use_case.execute(GetMeetingReviewQuery(meeting_id=_MEETING_ID, version=3))
    )

    assert latest.artifact == artifact
    assert explicit.artifact == artifact
    assert unit_of_work.commit_calls == 0


def test_missing_review_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="Meeting review not found"):
        asyncio.run(
            GetMeetingReviewUseCase(lambda: _UnitOfWork(None)).execute(
                GetMeetingReviewQuery(meeting_id=_MEETING_ID)
            )
        )
