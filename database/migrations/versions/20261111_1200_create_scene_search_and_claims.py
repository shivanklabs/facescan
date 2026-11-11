"""add scene embeddings, candidate history, claim enhancements and attempt limits

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11 12:00:00

- scene_embeddings: encrypted 768-dim SigLIP 2 scene vectors per photo (event-scoped, RLS).
- scene_search_candidates: short-lived candidate record verifying tapped faces (event-scoped, RLS).
- claims: add face_id (FK faces.id), reason, reviewed_at,
  and partial unique index on pending claims.
- matches: add source column ('search', 'claim', 'organiser').
- scene_search_attempts, claim_attempts: per-event per-user attempt tracking
  with advisory lock support.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. scene_embeddings
    op.create_table(
        "scene_embeddings",
        sa.Column(
            "photo_id", sa.UUID(), sa.ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # 2. scene_search_candidates
    op.create_table(
        "scene_search_candidates",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "photo_id", sa.UUID(), sa.ForeignKey("photos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "face_id", sa.UUID(), sa.ForeignKey("faces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_scene_search_candidates_lookup",
        "scene_search_candidates",
        ["event_id", "user_id", "photo_id", "face_id"],
        unique=False,
    )

    # 3. Enhance existing claims table
    op.add_column(
        "claims",
        sa.Column(
            "face_id", sa.UUID(), sa.ForeignKey("faces.id", ondelete="CASCADE"), nullable=True
        ),
    )
    op.add_column(
        "claims",
        sa.Column(
            "reason", sa.String(length=50), server_default="face_check_failed", nullable=False
        ),
    )
    op.add_column("claims", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    # Update status constraint to allow 'approved' in addition to 'accepted'
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claims_status")
    op.execute(
        "ALTER TABLE claims ADD CONSTRAINT ck_claims_status "
        "CHECK (status IN ('pending', 'accepted', 'approved', 'rejected'))"
    )
    # At most one pending claim per guest per face
    op.create_index(
        "uq_claims_pending_per_face",
        "claims",
        ["event_id", "user_id", "face_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    # 4. Enhance matches table with source
    op.add_column(
        "matches",
        sa.Column("source", sa.String(length=20), server_default="search", nullable=False),
    )

    # 5. scene_search_attempts
    op.create_table(
        "scene_search_attempts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_scene_search_attempts_rate",
        "scene_search_attempts",
        ["event_id", "user_id", "created_at"],
        unique=False,
    )

    # 6. claim_attempts
    op.create_table(
        "claim_attempts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_claim_attempts_rate",
        "claim_attempts",
        ["event_id", "user_id", "created_at"],
        unique=False,
    )

    _enable_policies()


def downgrade() -> None:
    _disable_policies()
    op.drop_table("claim_attempts")
    op.drop_table("scene_search_attempts")
    op.drop_column("matches", "source")
    op.drop_index("uq_claims_pending_per_face", table_name="claims")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claims_status")
    op.execute(
        "ALTER TABLE claims ADD CONSTRAINT ck_claims_status "
        "CHECK (status IN ('pending', 'accepted', 'rejected'))"
    )
    op.drop_column("claims", "reviewed_at")
    op.drop_column("claims", "reason")
    op.drop_column("claims", "face_id")
    op.drop_table("scene_search_candidates")
    op.drop_table("scene_embeddings")


NEW_EVENT_TABLES = (
    "scene_embeddings",
    "scene_search_candidates",
    "scene_search_attempts",
    "claim_attempts",
)
EVENT_MATCH = "event_id = nullif(current_setting('app.event_id', true), '')::uuid"


def _enable_policies() -> None:
    """Purpose: RLS on the new event tables."""
    for table in NEW_EVENT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_event_isolation ON {table} "
            f"USING ({EVENT_MATCH}) WITH CHECK ({EVENT_MATCH})"
        )


def _disable_policies() -> None:
    """Purpose: undo _enable_policies."""
    for table in NEW_EVENT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_event_isolation ON {table}")
