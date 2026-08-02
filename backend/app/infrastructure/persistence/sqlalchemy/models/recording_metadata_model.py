"""SQLAlchemy metadata for encrypted local recording references."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.models.mixins import TimestampMixin


class RecordingMetadataModel(Base, TimestampMixin):
    """Persist local recording metadata, never audio bytes or raw file paths."""

    __tablename__ = "recording_metadata"
    __table_args__ = (
        Index("ix_recording_metadata_meeting_id", "meeting_id"),
        Index("ix_recording_metadata_expires_at", "expires_at"),
        Index("ix_recording_metadata_deletion_status", "deletion_status"),
    )

    recording_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_anchor_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    deletion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encryption_format_version: Mapped[int] = mapped_column(Integer, nullable=False)
    container_format: Mapped[str] = mapped_column(String(16), nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    has_gaps: Mapped[bool] = mapped_column(Boolean, nullable=False)
    storage_directory_token: Mapped[str] = mapped_column(String(255), nullable=False)
    key_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    current_segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
