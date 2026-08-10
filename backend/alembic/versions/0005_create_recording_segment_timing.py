"""Create durable integer playback timing metadata for recording segments.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recording_segment_timing",
        sa.Column("recording_id", sa.String(length=36), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("start_sample", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recording_metadata.recording_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("recording_id", "segment_index"),
    )
    op.create_index(
        "ix_recording_segment_timing_recording_id",
        "recording_segment_timing",
        ["recording_id"],
    )
    op.create_index(
        "ix_recording_segment_timing_recording_index",
        "recording_segment_timing",
        ["recording_id", "segment_index"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recording_segment_timing_recording_index",
        table_name="recording_segment_timing",
    )
    op.drop_index(
        "ix_recording_segment_timing_recording_id",
        table_name="recording_segment_timing",
    )
    op.drop_table("recording_segment_timing")
