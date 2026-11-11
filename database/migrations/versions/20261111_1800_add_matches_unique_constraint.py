"""add unique constraint on matches (event_id, user_id, photo_id)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11 18:00:00

- matches: add unique constraint uq_matches_event_user_photo on (event_id, user_id, photo_id).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_matches_event_user_photo",
        "matches",
        ["event_id", "user_id", "photo_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_matches_event_user_photo", "matches", type_="unique")
