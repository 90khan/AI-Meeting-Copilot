"""SQLAlchemy persistence adapters and metadata."""

from app.infrastructure.persistence.sqlalchemy.meeting_repository import (
    SQLAlchemyMeetingRepository,
)

__all__ = ["SQLAlchemyMeetingRepository"]
