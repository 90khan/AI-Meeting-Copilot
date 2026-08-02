"""Create recording metadata.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create metadata-only local recording persistence."""

    op.create_table(
        "recording_metadata",
        sa.Column("recording_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("retention_policy", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capture_anchor_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("protected", sa.Boolean(), nullable=False),
        sa.Column("consent_confirmed", sa.Boolean(), nullable=False),
        sa.Column("consent_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_status", sa.String(length=32), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encryption_format_version", sa.Integer(), nullable=False),
        sa.Column("container_format", sa.String(length=16), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("has_gaps", sa.Boolean(), nullable=False),
        sa.Column("storage_directory_token", sa.String(length=255), nullable=False),
        sa.Column("key_reference", sa.String(length=255), nullable=False),
        sa.Column("current_segment_index", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recording_id"),
    )
    op.create_index(
        "ix_recording_metadata_meeting_id", "recording_metadata", ["meeting_id"]
    )
    op.create_index(
        "ix_recording_metadata_expires_at", "recording_metadata", ["expires_at"]
    )
    op.create_index(
        "ix_recording_metadata_deletion_status",
        "recording_metadata",
        ["deletion_status"],
    )


def downgrade() -> None:
    """Drop recording metadata and its indexes."""

    op.drop_index(
        "ix_recording_metadata_deletion_status", table_name="recording_metadata"
    )
    op.drop_index("ix_recording_metadata_expires_at", table_name="recording_metadata")
    op.drop_index("ix_recording_metadata_meeting_id", table_name="recording_metadata")
    op.drop_table("recording_metadata")
