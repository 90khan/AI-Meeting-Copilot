"""SQLAlchemy adapter for immutable Meeting translation artifacts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.dto.meeting_review import (
    MeetingTranslationArtifact,
    TranslationArtifactSegment,
    TranslationArtifactStatus,
)
from app.application.exceptions import ApplicationValidationError
from app.application.interfaces.meeting_translation_repository import (
    MeetingTranslationRepository,
)
from app.domain.value_objects import MeetingId
from app.infrastructure.persistence.sqlalchemy.models import (
    meeting_translation_artifact_model,
)

MeetingTranslationArtifactModel = (
    meeting_translation_artifact_model.MeetingTranslationArtifactModel
)


class SQLAlchemyMeetingTranslationRepository(MeetingTranslationRepository):
    """Persist artifacts through a caller-owned synchronous SQLAlchemy Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def save(self, artifact: MeetingTranslationArtifact) -> None:
        """Insert or update an artifact, flushing without taking commit ownership."""

        model = self._session.get(
            MeetingTranslationArtifactModel,
            str(artifact.artifact_id),
        )
        if model is None:
            model = MeetingTranslationArtifactModel(
                artifact_id=str(artifact.artifact_id)
            )
            self._session.add(model)
        self._apply(model, artifact)
        self._session.flush()

    async def get_by_id(self, artifact_id: UUID) -> MeetingTranslationArtifact | None:
        """Return an immutable artifact when its persisted identity exists."""

        model = self._session.get(MeetingTranslationArtifactModel, str(artifact_id))
        return None if model is None else self._to_artifact(model)

    async def get_latest_completed(
        self,
        meeting_id: MeetingId,
    ) -> MeetingTranslationArtifact | None:
        """Return the latest completed artifact using deterministic tie breakers."""

        statement = (
            select(MeetingTranslationArtifactModel)
            .where(
                MeetingTranslationArtifactModel.meeting_id == str(meeting_id),
                MeetingTranslationArtifactModel.status
                == TranslationArtifactStatus.COMPLETED.value,
            )
            .order_by(
                MeetingTranslationArtifactModel.created_at.desc(),
                MeetingTranslationArtifactModel.version.desc(),
                MeetingTranslationArtifactModel.artifact_id.desc(),
            )
            .limit(1)
        )
        model = self._session.scalar(statement)
        return None if model is None else self._to_artifact(model)

    async def get_latest_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
    ) -> int | None:
        """Return the highest artifact version for one Meeting and language."""

        statement = select(func.max(MeetingTranslationArtifactModel.version)).where(
            MeetingTranslationArtifactModel.meeting_id == str(meeting_id),
            MeetingTranslationArtifactModel.target_language == target_language,
        )
        latest_version = self._session.scalar(statement)
        return None if latest_version is None else int(latest_version)

    async def get_by_meeting_and_version(
        self,
        meeting_id: MeetingId,
        *,
        target_language: str,
        version: int,
    ) -> MeetingTranslationArtifact | None:
        """Return an exact immutable artifact version when it exists."""

        statement = select(MeetingTranslationArtifactModel).where(
            MeetingTranslationArtifactModel.meeting_id == str(meeting_id),
            MeetingTranslationArtifactModel.target_language == target_language,
            MeetingTranslationArtifactModel.version == version,
        )
        model = self._session.scalar(statement)
        return None if model is None else self._to_artifact(model)

    async def list_for_meeting(
        self,
        meeting_id: MeetingId,
    ) -> tuple[MeetingTranslationArtifact, ...]:
        """Return immutable artifacts in deterministic latest-first order."""

        statement = (
            select(MeetingTranslationArtifactModel)
            .where(MeetingTranslationArtifactModel.meeting_id == str(meeting_id))
            .order_by(
                MeetingTranslationArtifactModel.created_at.desc(),
                MeetingTranslationArtifactModel.version.desc(),
                MeetingTranslationArtifactModel.artifact_id.desc(),
            )
        )
        return tuple(
            self._to_artifact(model) for model in self._session.scalars(statement)
        )

    @staticmethod
    def _apply(
        model: MeetingTranslationArtifactModel,
        artifact: MeetingTranslationArtifact,
    ) -> None:
        model.meeting_id = str(artifact.meeting_id)
        model.version = artifact.version
        model.target_language = artifact.target_language
        model.status = artifact.status.value
        model.created_at = artifact.created_at
        model.completed_at = artifact.completed_at
        model.source_transcript_count = artifact.source_transcript_count
        model.segments = [
            {
                "transcript_id": str(segment.transcript_id),
                "source_text": segment.source_text,
                "translated_text": segment.translated_text,
            }
            for segment in artifact.segments
        ]
        model.provider_name = artifact.provider_name
        model.model_name = artifact.model_name
        model.prompt_version = artifact.prompt_version
        model.schema_version = artifact.schema_version
        model.failure_code = artifact.failure_code

    @classmethod
    def _to_artifact(
        cls,
        model: MeetingTranslationArtifactModel,
    ) -> MeetingTranslationArtifact:
        return MeetingTranslationArtifact(
            artifact_id=UUID(model.artifact_id),
            meeting_id=MeetingId(UUID(model.meeting_id)),
            version=model.version,
            target_language=model.target_language,
            status=TranslationArtifactStatus(model.status),
            created_at=cls._normalize_utc(model.created_at),
            completed_at=cls._normalize_optional_utc(model.completed_at),
            source_transcript_count=model.source_transcript_count,
            segments=cls._segments_from_json(model.segments),
            provider_name=model.provider_name,
            model_name=model.model_name,
            prompt_version=model.prompt_version,
            schema_version=model.schema_version,
            failure_code=model.failure_code,
        )

    @staticmethod
    def _segments_from_json(value: object) -> tuple[TranslationArtifactSegment, ...]:
        if not isinstance(value, list):
            raise ApplicationValidationError(
                "Persisted translation segments are invalid."
            )

        segments: list[TranslationArtifactSegment] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "transcript_id",
                "source_text",
                "translated_text",
            }:
                raise ApplicationValidationError(
                    "Persisted translation segments are invalid."
                )
            segments.append(
                TranslationArtifactSegment(
                    transcript_id=UUID(str(item["transcript_id"])),
                    source_text=item["source_text"],
                    translated_text=item["translated_text"],
                )
            )
        return tuple(segments)

    @staticmethod
    def _normalize_optional_utc(value: datetime | None) -> datetime | None:
        return (
            None
            if value is None
            else SQLAlchemyMeetingTranslationRepository._normalize_utc(value)
        )

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        if value.utcoffset() != timedelta(0):
            raise ApplicationValidationError(
                "Persisted translation timestamps must use UTC."
            )
        return value
