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
                "meeting_review_artifacts",
                "meeting_translation_artifacts",
                "recording_metadata",
                "recording_segment_timing",
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

            assert {
                column["name"]
                for column in inspector.get_columns("meeting_translation_artifacts")
            } == {
                "artifact_id",
                "meeting_id",
                "version",
                "target_language",
                "status",
                "created_at",
                "completed_at",
                "source_transcript_count",
                "segments",
                "provider_name",
                "model_name",
                "prompt_version",
                "schema_version",
                "failure_code",
            }
            assert any(
                constraint["column_names"]
                == ["meeting_id", "target_language", "version"]
                for constraint in inspector.get_unique_constraints(
                    "meeting_translation_artifacts"
                )
            )
            assert {
                index["name"]
                for index in inspector.get_indexes("meeting_translation_artifacts")
            } == {
                "ix_meeting_translation_artifacts_meeting_id",
                "ix_meeting_translation_artifacts_meeting_language",
                "ix_meeting_translation_artifacts_status",
                "ix_meeting_translation_artifacts_created_at",
            }
            assert {
                column["name"]
                for column in inspector.get_columns("meeting_review_artifacts")
            } == {
                "artifact_id",
                "meeting_id",
                "version",
                "review_type",
                "status",
                "created_at",
                "completed_at",
                "source_transcript_count",
                "provider_name",
                "model_name",
                "prompt_version",
                "schema_version",
                "failure_code",
                "content_json",
            }
            assert any(
                constraint["column_names"] == ["meeting_id", "review_type", "version"]
                for constraint in inspector.get_unique_constraints(
                    "meeting_review_artifacts"
                )
            )
            assert {
                index["name"]
                for index in inspector.get_indexes("meeting_review_artifacts")
            } == {
                "ix_meeting_review_artifacts_meeting_id",
                "ix_meeting_review_artifacts_meeting_type",
                "ix_meeting_review_artifacts_status",
                "ix_meeting_review_artifacts_created_at",
            }
            assert {
                column["name"]
                for column in inspector.get_columns("recording_segment_timing")
            } == {
                "recording_id",
                "segment_index",
                "sample_count",
                "start_sample",
            }
            primary_key = inspector.get_pk_constraint("recording_segment_timing")
            assert primary_key["constrained_columns"] == [
                "recording_id",
                "segment_index",
            ]
            timing_foreign_keys = inspector.get_foreign_keys("recording_segment_timing")
            assert len(timing_foreign_keys) == 1
            assert timing_foreign_keys[0]["referred_table"] == "recording_metadata"
            assert timing_foreign_keys[0]["constrained_columns"] == ["recording_id"]
            assert timing_foreign_keys[0]["options"]["ondelete"] == "CASCADE"
            assert {
                index["name"]
                for index in inspector.get_indexes("recording_segment_timing")
            } == {
                "ix_recording_segment_timing_recording_id",
                "ix_recording_segment_timing_recording_index",
            }
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
