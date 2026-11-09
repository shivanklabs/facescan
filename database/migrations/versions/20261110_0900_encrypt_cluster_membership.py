"""encrypt cluster membership

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10 09:00:00

Cluster membership moves from plaintext columns (faces.cluster_id, faces.attached_weak) into
the cluster's sealed payload (clusters.payload_encrypted: prototypes + member ids), encrypted
under the event key. Existing clusters cannot be re-encrypted in SQL (keys live only in the
app), so they are removed and every searchable event is queued for re-indexing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Runs as the app role under FORCEd RLS: read events in the admin scope, then switch to each
# event's scope to update it and queue its re-index job.
REQUEUE_SEARCHABLE_EVENTS = """
DO $$
DECLARE
    ids uuid[];
    e uuid;
BEGIN
    PERFORM set_config('app.scope', 'admin', true);
    SELECT coalesce(array_agg(id), '{}') INTO ids FROM events WHERE status = 'searchable';
    PERFORM set_config('app.scope', '', true);
    FOREACH e IN ARRAY ids LOOP
        PERFORM set_config('app.event_id', e::text, true);
        UPDATE events SET status = 'indexing' WHERE id = e;
        INSERT INTO jobs (event_id, type, payload)
        VALUES (e, 'index_event', '{"reason": "schema_migration"}');
    END LOOP;
    PERFORM set_config('app.event_id', '', true);
END $$;
"""


def upgrade() -> None:
    """Purpose: drop plaintext membership; clusters carry an encrypted payload instead."""
    op.drop_index("ix_faces_cluster_id", table_name="faces")
    op.drop_constraint("fk_faces_cluster_id_clusters", "faces", type_="foreignkey")
    op.drop_column("faces", "cluster_id")
    op.drop_column("faces", "attached_weak")
    op.execute("TRUNCATE clusters")  # rebuilt by the queued index_event jobs
    op.alter_column("clusters", "prototype_encrypted", new_column_name="payload_encrypted")
    op.execute(REQUEUE_SEARCHABLE_EVENTS)


def downgrade() -> None:
    """Purpose: restore plaintext membership columns (clusters rebuilt by re-indexing)."""
    op.execute("TRUNCATE clusters")
    op.alter_column("clusters", "payload_encrypted", new_column_name="prototype_encrypted")
    op.add_column(
        "faces",
        sa.Column("attached_weak", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("faces", sa.Column("cluster_id", postgresql.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_faces_cluster_id_clusters",
        "faces",
        "clusters",
        ["cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_faces_cluster_id", "faces", ["cluster_id"])
    op.execute(REQUEUE_SEARCHABLE_EVENTS)
