"""Audit trail writer. Details hold counts and ids only: never images, vectors, emails or tokens."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from app.features.activity_log.models import AuditLog

AuditDetails = Mapping[str, int | str | bool | None]


def record(
    session: Session,
    *,
    event_id: UUID | None,
    actor: str,
    action: str,
    target: str | None = None,
    details: AuditDetails | None = None,
) -> None:
    """
    Purpose: append one audit row in the caller's transaction (event scope, or admin scope
        when event_id is None).
    Side effects: inserts into audit_log.
    """
    session.add(
        AuditLog(
            event_id=event_id,
            actor=actor[:100],
            action=action[:100],
            target=None if target is None else target[:200],
            details=dict(details or {}),
        )
    )
