"""Tests for Meeting SQLAlchemy ORM metadata."""

from app.infrastructure.persistence.sqlalchemy.models import MeetingModel
from sqlalchemy import DateTime, String


def test_meeting_model_uses_the_expected_table_name() -> None:
    """The Meeting model maps to the meetings table."""

    assert MeetingModel.__tablename__ == "meetings"


def test_meeting_model_has_the_expected_columns() -> None:
    """The Meeting model declares only its foundational persistence fields."""

    assert set(MeetingModel.__table__.columns.keys()) == {
        "id",
        "name",
        "status",
        "started_at",
        "ended_at",
        "created_at",
        "updated_at",
    }


def test_meeting_model_columns_have_expected_types_and_nullability() -> None:
    """Meeting fields retain their required SQLAlchemy metadata."""

    columns = MeetingModel.__table__.c

    assert isinstance(columns.id.type, String)
    assert columns.id.type.length == 36
    assert columns.id.primary_key is True
    assert columns.name.type.length == 255
    assert columns.name.nullable is False
    assert columns.status.type.length == 32
    assert columns.status.nullable is False
    assert columns.started_at.nullable is True
    assert columns.ended_at.nullable is True


def test_timestamp_mixin_columns_are_timezone_aware() -> None:
    """Timestamp columns are present with timezone-aware SQL types."""

    columns = MeetingModel.__table__.c

    assert isinstance(columns.created_at.type, DateTime)
    assert columns.created_at.type.timezone is True
    assert columns.updated_at.type.timezone is True
    assert columns.created_at.server_default is not None
    assert columns.updated_at.server_default is not None
    assert columns.updated_at.onupdate is not None
