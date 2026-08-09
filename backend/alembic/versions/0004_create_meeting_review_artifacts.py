"""Create Meeting review artifacts.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create versioned Meeting review artifact persistence."""

    op.create_table(
        "meeting_review_artifacts",
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_transcript_count", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint(
            "meeting_id",
            "review_type",
            "version",
            name="uq_meeting_review_artifacts_meeting_type_version",
        ),
    )
    op.create_index(
        "ix_meeting_review_artifacts_meeting_id",
        "meeting_review_artifacts",
        ["meeting_id"],
    )
    op.create_index(
        "ix_meeting_review_artifacts_meeting_type",
        "meeting_review_artifacts",
        ["meeting_id", "review_type"],
    )
    op.create_index(
        "ix_meeting_review_artifacts_status",
        "meeting_review_artifacts",
        ["status"],
    )
    op.create_index(
        "ix_meeting_review_artifacts_created_at",
        "meeting_review_artifacts",
        ["created_at"],
    )


def downgrade() -> None:
    """Drop Meeting review artifacts and their indexes."""

    op.drop_index(
        "ix_meeting_review_artifacts_created_at",
        table_name="meeting_review_artifacts",
    )
    op.drop_index(
        "ix_meeting_review_artifacts_status",
        table_name="meeting_review_artifacts",
    )
    op.drop_index(
        "ix_meeting_review_artifacts_meeting_type",
        table_name="meeting_review_artifacts",
    )
    op.drop_index(
        "ix_meeting_review_artifacts_meeting_id",
        table_name="meeting_review_artifacts",
    )
    op.drop_table("meeting_review_artifacts")
