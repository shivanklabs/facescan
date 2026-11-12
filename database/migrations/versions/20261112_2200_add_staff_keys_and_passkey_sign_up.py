"""add staff keys and passkey-only guest sign-up (no emails for anyone)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-12 22:00:00

- users.can_create_events, users.is_admin: staff rights set by a redeemed staff key (instead
  of email allowlists). Both default false; existing rows are unchanged.
- auth_challenges: purpose 'guest_register' and a pending_user_id (no foreign key): a guest
  account is created only when its passkey is verified, never before.
- staff_keys: one-time keys FaceScan generates for staff. Only a SHA-256 of the key is stored;
  the key itself is shown once. role admin / organiser / photographer (photographer keys
  belong to one event). Not event-scoped data (like sessions), so no row-level policy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSE_CHECK = "ck_auth_challenges_purpose"


def upgrade() -> None:
    """Purpose: add the columns and the staff_keys table. Side effects: schema only."""
    op.add_column(
        "users",
        sa.Column("can_create_events", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false")
    )

    op.add_column(
        "auth_challenges",
        sa.Column("pending_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint(op.f(PURPOSE_CHECK), "auth_challenges", type_="check")
    op.create_check_constraint(
        op.f(PURPOSE_CHECK),
        "auth_challenges",
        "purpose IN ('register', 'authenticate', 'guest_register')",
    )

    op.create_table(
        "staff_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "events.id", ondelete="CASCADE", name=op.f("fk_staff_keys_event_id_events")
            ),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name=op.f("fk_staff_keys_created_by_users")
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name=op.f("fk_staff_keys_used_by_users")
            ),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_keys")),
        sa.CheckConstraint(
            "role IN ('admin', 'organiser', 'photographer')", name=op.f("ck_staff_keys_role")
        ),
        sa.CheckConstraint(
            "(role = 'photographer') = (event_id IS NOT NULL)",
            name=op.f("ck_staff_keys_photographer_event"),
        ),
        sa.UniqueConstraint("key_hash", name=op.f("uq_staff_keys_key_hash")),
    )
    op.create_index("ix_staff_keys_created_by", "staff_keys", ["created_by"])


def downgrade() -> None:
    """Purpose: remove what upgrade added. Side effects: drops staff_keys and the columns."""
    op.drop_index("ix_staff_keys_created_by", table_name="staff_keys")
    op.drop_table("staff_keys")
    op.drop_constraint(op.f(PURPOSE_CHECK), "auth_challenges", type_="check")
    op.execute("DELETE FROM auth_challenges WHERE purpose = 'guest_register'")
    op.create_check_constraint(
        op.f(PURPOSE_CHECK), "auth_challenges", "purpose IN ('register', 'authenticate')"
    )
    op.drop_column("auth_challenges", "pending_user_id")
    op.drop_column("users", "is_admin")
    op.drop_column("users", "can_create_events")
