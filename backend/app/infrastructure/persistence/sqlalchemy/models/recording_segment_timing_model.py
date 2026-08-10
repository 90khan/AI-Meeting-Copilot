"""Durable integer sample offsets for retained recording segments."""

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class RecordingSegmentTimingModel(Base):
    __tablename__ = "recording_segment_timing"
    __table_args__ = (
        Index("ix_recording_segment_timing_recording_id", "recording_id"),
        Index(
            "ix_recording_segment_timing_recording_index",
            "recording_id",
            "segment_index",
        ),
    )

    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recording_metadata.recording_id", ondelete="CASCADE"),
        primary_key=True,
    )
    segment_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    start_sample: Mapped[int] = mapped_column(Integer, nullable=False)
