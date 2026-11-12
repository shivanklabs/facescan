"""change claims.face_id foreign key from ON DELETE CASCADE to ON DELETE SET NULL

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 14:00:00

- claims: a full re-index deletes and recreates faces. With CASCADE that deleted guests'
  claims. With SET NULL the claim stays and only loses its face box. No rows are changed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_claims_face_id_faces"


def _recreate(ondelete: str) -> None:
    """
    Purpose: replace the claims.face_id foreign key with the given delete rule.
    Inputs: ondelete ("SET NULL" or "CASCADE").
    Outputs: None.
    Side effects: drops and re-adds one constraint; no rows are changed.
    """
    op.drop_constraint(FK_NAME, "claims", type_="foreignkey")
    op.create_foreign_key(FK_NAME, "claims", "faces", ["face_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _recreate("SET NULL")


def downgrade() -> None:
    _recreate("CASCADE")
