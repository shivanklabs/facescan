"""allow 'google' as a session sign-in method

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12 18:00:00

- sessions.auth_method: add 'google' (Google sign-in, milestone 7). No rows are changed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK_NAME = "ck_sessions_auth_method"


def _replace_check(allowed: str) -> None:
    """
    Purpose: replace the sessions.auth_method check constraint.
    Inputs: SQL list of allowed values. Outputs: None.
    Side effects: drops and re-adds one constraint; no rows are changed.
    """
    op.drop_constraint(op.f(CHECK_NAME), "sessions", type_="check")
    op.create_check_constraint(op.f(CHECK_NAME), "sessions", f"auth_method IN ({allowed})")


def upgrade() -> None:
    _replace_check("'magic_link', 'passkey', 'google'")


def downgrade() -> None:
    _replace_check("'magic_link', 'passkey'")
