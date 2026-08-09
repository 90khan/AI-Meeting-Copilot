"""Persistence contract for immutable Meeting translation artifacts."""

from typing import Protocol
from uuid import UUID

from app.application.dto.meeting_review import MeetingTranslationArtifact
from app.domain.value_objects import MeetingId


class MeetingTranslationRepository(Protocol):
    """Store and retrieve versioned Turkish Meeting translation artifacts."""

    async def save(self, artifact: MeetingTranslationArtifact) -> None:
        """Save an artifact without committing the caller-owned transaction."""

        ...

    async def get_by_id(self, artifact_id: UUID) -> MeetingTranslationArtifact | None:
        """Return an artifact by stable identity when it exists."""

        ...

    async def get_latest_completed(
        self,
        meeting_id: MeetingId,
    ) -> MeetingTranslationArtifact | None:
        """Return the most recently created completed artifact for a Meeting."""

        ...

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
    ) -> int | None:
        """Return the highest persisted version for a Meeting and language."""

        ...

    async def get_by_meeting_and_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
        version: int,
    ) -> MeetingTranslationArtifact | None:
        """Return one exact persisted version for a Meeting and language."""

        ...

    async def list_for_meeting(
        self,
        meeting_id: MeetingId,
    ) -> tuple[MeetingTranslationArtifact, ...]:
        """Return all Meeting artifacts in deterministic latest-first order."""

        ...
