"""add camera offsets, upload sessions, section times, and camera fields

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11 17:00:00

- event_sections: add starts_at, ends_at (DateTime(timezone=True)).
- photos: add camera_make, camera_model, camera_serial, corrected_time.
- camera_offsets: table for per-camera clock sync offsets (event-scoped, RLS).
- upload_sessions: table for resumable chunked uploads (event-scoped, RLS).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_MATCH = "event_id = nullif(current_setting('app.event_id', true), '')::uuid"


def upgrade() -> None:
    """
    Purpose: Apply migration 0006 schema changes (camera sync, sections, upload sessions).
    Inputs: None.
    Outputs: None.
    Side Effects: Alters event_sections, photos; creates camera_offsets and upload_sessions tables.
    """
    # 1. event_sections starts_at, ends_at
    op.add_column(
        "event_sections", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("event_sections", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))

    # 2. photos camera info and corrected_time
    op.add_column("photos", sa.Column("camera_make", sa.String(length=64), nullable=True))
    op.add_column("photos", sa.Column("camera_model", sa.String(length=64), nullable=True))
    op.add_column("photos", sa.Column("camera_serial", sa.String(length=64), nullable=True))
    op.add_column("photos", sa.Column("corrected_time", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_photos_event_corrected_time", "photos", ["event_id", "corrected_time"], unique=False
    )

    # 3. camera_offsets table
    op.create_table(
        "camera_offsets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("camera_model", sa.String(length=64), nullable=False),
        sa.Column("camera_serial", sa.String(length=64), nullable=True),
        sa.Column("offset_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_camera_offsets_event_model",
        "camera_offsets",
        ["event_id", "camera_model", "camera_serial"],
        unique=False,
    )
    op.execute("ALTER TABLE camera_offsets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE camera_offsets FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY camera_offsets_event_isolation ON camera_offsets "
        f"USING ({EVENT_MATCH}) WITH CHECK ({EVENT_MATCH})"
    )

    # 4. upload_sessions table
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "collection_id",
            sa.UUID(),
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "received_chunks", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.String(length=20), server_default="in_progress", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute("ALTER TABLE upload_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE upload_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY upload_sessions_event_isolation ON upload_sessions "
        f"USING ({EVENT_MATCH}) WITH CHECK ({EVENT_MATCH})"
    )


def downgrade() -> None:
    """
    Purpose: Revert migration 0006 schema changes.
    Inputs: None.
    Outputs: None.
    Side Effects: Drops tables camera_offsets, upload_sessions; drops added columns.
    """
    op.execute("DROP POLICY IF EXISTS upload_sessions_event_isolation ON upload_sessions")
    op.drop_table("upload_sessions")

    op.execute("DROP POLICY IF EXISTS camera_offsets_event_isolation ON camera_offsets")
    op.drop_table("camera_offsets")

    op.drop_index("ix_photos_event_corrected_time", table_name="photos")
    op.drop_column("photos", "corrected_time")
    op.drop_column("photos", "camera_serial")
    op.drop_column("photos", "camera_model")
    op.drop_column("photos", "camera_make")

    op.drop_column("event_sections", "ends_at")
    op.drop_column("event_sections", "starts_at")
