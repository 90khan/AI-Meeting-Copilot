"""SQLAlchemy ORM metadata for Meeting persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from .transcript_entry_model import TranscriptEntryModel


class MeetingModel(Base, TimestampMixin):
    """Database representation of a Meeting aggregate."""

    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcripts: Mapped[list[TranscriptEntryModel]] = relationship(
        "TranscriptEntryModel",
        back_populates="meeting",
        cascade="all, delete-orphan",
        order_by="TranscriptEntryModel.sequence",
    )
