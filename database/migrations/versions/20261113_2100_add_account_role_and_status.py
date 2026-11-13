"""add account role, status and name; who assigned a photographer

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-13 21:00:00

Owner decisions 13 Nov 2026 (one common login, self-registration with admin approval):
- users.role: guest / organiser / photographer / admin (the server decides it; never the
  browser). users.status: active / pending / rejected / suspended. users.display_name.
  users.status_changed_at: when an admin last approved / rejected / suspended the account.
- The old staff flags are folded into role (is_admin -> admin, can_create_events ->
  organiser) and then dropped, so there is one source of truth. Photographers who joined
  with a key keep working through their event membership (row-level security stops this
  migration from reading event_members, so their role stays 'guest').
- event_members.assigned_by: who assigned a photographer to an event (NULL for older rows
  and for self-joins). No separate assignments table: event_members already is one.
- Index on (role, status) for the admin's pending-approvals list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Purpose: add role/status/name, fold the staff flags into role, add assigned_by."""
    op.add_column("users", sa.Column("display_name", sa.String(80), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(20), nullable=False, server_default="guest"))
    op.add_column(
        "users", sa.Column("status", sa.String(20), nullable=False, server_default="active")
    )
    op.add_column(
        "users", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_users_role"), "users", "role IN ('guest', 'organiser', 'photographer', 'admin')"
    )
    op.create_check_constraint(
        op.f("ck_users_status"), "users", "status IN ('active', 'pending', 'rejected', 'suspended')"
    )
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin")
    op.execute("UPDATE users SET role = 'organiser' WHERE can_create_events AND NOT is_admin")
    op.drop_column("users", "is_admin")
    op.drop_column("users", "can_create_events")
    op.create_index("ix_users_role_status", "users", ["role", "status"])

    op.add_column(
        "event_members",
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name=op.f("fk_event_members_assigned_by_users")
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Purpose: back to the two staff flags (rebuilt from role). Side effects: schema + data."""
    op.drop_column("event_members", "assigned_by")
    op.drop_index("ix_users_role_status", table_name="users")
    op.add_column(
        "users",
        sa.Column("can_create_events", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false")
    )
    op.execute("UPDATE users SET is_admin = true, can_create_events = true WHERE role = 'admin'")
    op.execute(
        "UPDATE users SET can_create_events = true WHERE role = 'organiser' AND status = 'active'"
    )
    op.drop_constraint(op.f("ck_users_status"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.drop_column("users", "status_changed_at")
    op.drop_column("users", "status")
    op.drop_column("users", "role")
    op.drop_column("users", "display_name")
