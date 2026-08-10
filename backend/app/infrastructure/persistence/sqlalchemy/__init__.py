"""SQLAlchemy persistence adapters and metadata."""

from app.infrastructure.persistence.sqlalchemy import (
    meeting_review_artifact_repository,
    recording_segment_timing_repository,
)
from app.infrastructure.persistence.sqlalchemy.meeting_repository import (
    SQLAlchemyMeetingRepository,
)
from app.infrastructure.persistence.sqlalchemy.meeting_review_repository import (
    SQLAlchemyMeetingReviewRepository,
)
from app.infrastructure.persistence.sqlalchemy.meeting_translation_repository import (
    SQLAlchemyMeetingTranslationRepository,
)
from app.infrastructure.persistence.sqlalchemy.recording_repository import (
    SQLAlchemyRecordingRepository,
)
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SQLAlchemyUnitOfWork

SQLAlchemyMeetingReviewArtifactRepository = (
    meeting_review_artifact_repository.SQLAlchemyMeetingReviewArtifactRepository
)
SQLAlchemyRecordingSegmentTimingRepository = (
    recording_segment_timing_repository.SQLAlchemyRecordingSegmentTimingRepository
)

__all__ = [
    "SQLAlchemyMeetingRepository",
    "SQLAlchemyMeetingReviewArtifactRepository",
    "SQLAlchemyMeetingReviewRepository",
    "SQLAlchemyMeetingTranslationRepository",
    "SQLAlchemyRecordingRepository",
    "SQLAlchemyRecordingSegmentTimingRepository",
    "SQLAlchemyUnitOfWork",
]
