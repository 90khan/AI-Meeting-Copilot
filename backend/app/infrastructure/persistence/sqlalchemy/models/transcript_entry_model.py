"""SQLAlchemy ORM metadata for Meeting transcript entries."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.infrastructure.persistence.sqlalchemy.models.meeting_model import (
        MeetingModel,
    )


class TranscriptEntryModel(Base, TimestampMixin):
    """Database representation of an immutable Meeting transcript entry."""

    __tablename__ = "transcript_entries"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "sequence",
            name="uq_transcript_entries_meeting_id_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    speaker: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    meeting: Mapped[MeetingModel] = relationship(
        "MeetingModel",
        back_populates="transcripts",
    )
