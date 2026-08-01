"""SQLAlchemy ORM metadata for Meeting persistence."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.infrastructure.persistence.sqlalchemy.models.mixins import TimestampMixin


class MeetingModel(Base, TimestampMixin):
    """Database representation of a Meeting aggregate."""

    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
