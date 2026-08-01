"""Integration tests for Alembic migrations."""

from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from sqlalchemy import create_engine, inspect


def test_initial_migration_upgrades_and_downgrades_sqlite(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """The initial migration creates and removes the expected SQLite schema."""

    database_path = tmp_path / "alembic-test.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("AI_MEETING_COPILOT_DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "head")

        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)

            assert set(inspector.get_table_names()) >= {
                "alembic_version",
                "meetings",
                "transcript_entries",
            }
            assert {column["name"] for column in inspector.get_columns("meetings")} == {
                "id",
                "name",
                "status",
                "started_at",
                "ended_at",
                "created_at",
                "updated_at",
            }
            assert {
                column["name"] for column in inspector.get_columns("transcript_entries")
            } == {
                "id",
                "meeting_id",
                "speaker",
                "text",
                "timestamp",
                "sequence",
                "created_at",
                "updated_at",
            }

            foreign_keys = inspector.get_foreign_keys("transcript_entries")
            assert foreign_keys[0]["referred_table"] == "meetings"
            assert foreign_keys[0]["constrained_columns"] == ["meeting_id"]
            assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"
            assert any(
                constraint["column_names"] == ["meeting_id", "sequence"]
                for constraint in inspector.get_unique_constraints("transcript_entries")
            )
        finally:
            engine.dispose()

        command.downgrade(config, "base")

        engine = create_engine(database_url)
        try:
            assert inspect(engine).get_table_names() == ["alembic_version"]
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
