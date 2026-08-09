"""SQLAlchemy metadata for versioned Meeting translation artifacts."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class MeetingTranslationArtifactModel(Base):
    """Persist immutable Meeting translation artifact data and ordered segments."""

    __tablename__ = "meeting_translation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "target_language",
            "version",
            name="uq_meeting_translation_artifacts_meeting_language_version",
        ),
        Index("ix_meeting_translation_artifacts_meeting_id", "meeting_id"),
        Index(
            "ix_meeting_translation_artifacts_meeting_language",
            "meeting_id",
            "target_language",
        ),
        Index("ix_meeting_translation_artifacts_status", "status"),
        Index("ix_meeting_translation_artifacts_created_at", "created_at"),
    )

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_language: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_transcript_count: Mapped[int] = mapped_column(Integer, nullable=False)
    segments: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
