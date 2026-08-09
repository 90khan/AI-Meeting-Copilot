"""Persistence contract for immutable Meeting review artifacts."""

from typing import Protocol
from uuid import UUID

from app.application.dto.meeting_review import MeetingReviewArtifact
from app.domain.value_objects import MeetingId


class MeetingReviewArtifactRepository(Protocol):
    """Store and retrieve versioned Meeting interview-review artifacts."""

    async def save(self, artifact: MeetingReviewArtifact) -> None:
        """Save an artifact without committing the caller-owned transaction."""

        ...

    async def get_by_id(self, artifact_id: UUID) -> MeetingReviewArtifact | None:
        """Return an artifact by its stable identity when it exists."""

        ...

    async def get_by_meeting_and_version(
        self,
        meeting_id: MeetingId,
        review_type: str,
        version: int,
    ) -> MeetingReviewArtifact | None:
        """Return one exact persisted version for a Meeting and review type."""

        ...

    async def get_latest_completed(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> MeetingReviewArtifact | None:
        """Return the highest-version completed review artifact when one exists."""

        ...

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> int:
        """Return the highest persisted version across every lifecycle state."""

        ...

    async def list_for_meeting(
        self,
        meeting_id: MeetingId,
        review_type: str,
    ) -> tuple[MeetingReviewArtifact, ...]:
        """Return artifacts in deterministic latest-version-first order."""

        ...
