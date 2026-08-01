"""Tests for transcript-entry SQLAlchemy ORM metadata."""

from app.infrastructure.persistence.sqlalchemy.models import (
    MeetingModel,
    TranscriptEntryModel,
)
from sqlalchemy import DateTime, String, UniqueConstraint


def test_transcript_entry_model_uses_the_expected_table_name() -> None:
    """The transcript-entry model maps to transcript_entries."""

    assert TranscriptEntryModel.__tablename__ == "transcript_entries"


def test_transcript_entry_model_has_the_expected_columns() -> None:
    """The model declares its transcript and inherited timestamp columns."""

    assert set(TranscriptEntryModel.__table__.columns.keys()) == {
        "id",
        "meeting_id",
        "speaker",
        "text",
        "timestamp",
        "sequence",
        "created_at",
        "updated_at",
    }


def test_transcript_entry_model_columns_have_expected_metadata() -> None:
    """Transcript columns retain their key and nullability rules."""

    columns = TranscriptEntryModel.__table__.c
    (foreign_key,) = columns.meeting_id.foreign_keys

    assert isinstance(columns.id.type, String)
    assert columns.id.type.length == 36
    assert columns.id.primary_key is True
    assert columns.meeting_id.nullable is False
    assert foreign_key.target_fullname == "meetings.id"
    assert foreign_key.ondelete == "CASCADE"
    assert columns.speaker.nullable is False
    assert columns.speaker.type.length == 255
    assert columns.text.nullable is False
    assert columns.timestamp.nullable is False
    assert isinstance(columns.timestamp.type, DateTime)
    assert columns.timestamp.type.timezone is True
    assert columns.sequence.nullable is False


def test_transcript_entry_model_enforces_unique_meeting_sequence() -> None:
    """A Meeting cannot contain two entries at the same sequence position."""

    unique_constraints = (
        constraint
        for constraint in TranscriptEntryModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    )

    assert any(
        tuple(column.name for column in constraint.columns)
        == ("meeting_id", "sequence")
        for constraint in unique_constraints
    )


def test_meeting_transcript_relationship_is_ordered_and_orphan_deleting() -> None:
    """Meetings own ordered transcript rows through a bidirectional relationship."""

    relationship = MeetingModel.transcripts.property

    assert relationship.mapper.class_ is TranscriptEntryModel
    assert relationship.back_populates == "meeting"
    assert tuple(column.name for column in relationship.order_by) == ("sequence",)
    assert relationship.cascade.delete_orphan is True
    assert TranscriptEntryModel.meeting.property.back_populates == "transcripts"
