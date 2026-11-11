"""add cannot_links, update matches score_band constraint, and add photo hashes

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11 16:00:00

- matches: update ck_matches_score_band to include 'not_me' and 'withdrawn'.
- cannot_links: table for recording excluded faces/photos per user and event (event-scoped, RLS).
- photos: add sha256 (String(64)), phash (String(16)), and is_near_duplicate (Boolean).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_MATCH = "event_id = nullif(current_setting('app.event_id', true), '')::uuid"


def upgrade() -> None:
    # 1. Update matches score_band check constraint
    op.execute("ALTER TABLE matches DROP CONSTRAINT IF EXISTS ck_matches_score_band")
    op.execute("ALTER TABLE matches DROP CONSTRAINT IF EXISTS score_band")
    op.create_check_constraint(
        "ck_matches_score_band",
        "matches",
        "score_band IN ('release', 'borderline', 'none', 'not_me', 'withdrawn')",
    )

    # 2. cannot_links table
    op.create_table(
        "cannot_links",
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
            "photo_id",
            sa.UUID(),
            sa.ForeignKey("photos.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "face_id",
            sa.UUID(),
            sa.ForeignKey("faces.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_cannot_links_event_user_photo",
        "cannot_links",
        ["event_id", "user_id", "photo_id"],
        unique=False,
    )

    # 3. Enable RLS on cannot_links
    op.execute("ALTER TABLE cannot_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cannot_links FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY cannot_links_event_isolation ON cannot_links "
        f"USING ({EVENT_MATCH}) WITH CHECK ({EVENT_MATCH})"
    )

    # 4. photos columns for duplicate and near-duplicate detection
    op.add_column("photos", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("photos", sa.Column("phash", sa.String(length=16), nullable=True))
    op.add_column(
        "photos",
        sa.Column(
            "is_near_duplicate",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index("ix_photos_event_sha256", "photos", ["event_id", "sha256"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_photos_event_sha256", table_name="photos")
    op.drop_column("photos", "is_near_duplicate")
    op.drop_column("photos", "phash")
    op.drop_column("photos", "sha256")

    op.execute("DROP POLICY IF EXISTS cannot_links_event_isolation ON cannot_links")
    op.drop_table("cannot_links")

    op.execute("ALTER TABLE matches DROP CONSTRAINT IF EXISTS ck_matches_score_band")
    op.create_check_constraint(
        "ck_matches_score_band",
        "matches",
        "score_band IN ('release', 'borderline', 'none')",
    )
