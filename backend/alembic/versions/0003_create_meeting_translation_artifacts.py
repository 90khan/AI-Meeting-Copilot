"""Create Meeting translation artifacts.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create versioned Turkish Meeting translation artifact persistence."""

    op.create_table(
        "meeting_translation_artifacts",
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("target_language", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_transcript_count", sa.Integer(), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint(
            "meeting_id",
            "target_language",
            "version",
            name="uq_meeting_translation_artifacts_meeting_language_version",
        ),
    )
    op.create_index(
        "ix_meeting_translation_artifacts_meeting_id",
        "meeting_translation_artifacts",
        ["meeting_id"],
    )
    op.create_index(
        "ix_meeting_translation_artifacts_meeting_language",
        "meeting_translation_artifacts",
        ["meeting_id", "target_language"],
    )
    op.create_index(
        "ix_meeting_translation_artifacts_status",
        "meeting_translation_artifacts",
        ["status"],
    )
    op.create_index(
        "ix_meeting_translation_artifacts_created_at",
        "meeting_translation_artifacts",
        ["created_at"],
    )


def downgrade() -> None:
    """Drop Meeting translation artifacts and their indexes."""

    op.drop_index(
        "ix_meeting_translation_artifacts_created_at",
        table_name="meeting_translation_artifacts",
    )
    op.drop_index(
        "ix_meeting_translation_artifacts_status",
        table_name="meeting_translation_artifacts",
    )
    op.drop_index(
        "ix_meeting_translation_artifacts_meeting_language",
        table_name="meeting_translation_artifacts",
    )
    op.drop_index(
        "ix_meeting_translation_artifacts_meeting_id",
        table_name="meeting_translation_artifacts",
    )
    op.drop_table("meeting_translation_artifacts")
