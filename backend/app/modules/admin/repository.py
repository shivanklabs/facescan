"""Database queries for admin; every query is scoped to one event_id where applicable."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.models import Job
from app.modules.events.models import Event
from app.modules.gallery.models import Claim
from app.modules.ingest.models import Photo
from app.modules.matching.models import CannotLink


def list_events_admin(session: Session) -> list[Event]:
    """
    Purpose: List all events with metadata in admin scope.
    Inputs: session (Session with ADMIN_SCOPE).
    Outputs: list of Event models ordered by creation time descending.
    Side effects: none.
    """
    stmt = select(Event).order_by(Event.created_at.desc())
    return list(session.execute(stmt).scalars().all())


def get_event_health_counts(session: Session, event_id: UUID) -> dict[str, int]:
    """
    Purpose: Aggregate photo status, job failure, claim, and not_me counts for one event.
    Inputs: session (Session scoped to event_id), event_id (UUID).
    Outputs: dict with counts for photos_quarantined, photos_approved, photos_indexed,
        photos_failed, photos_total, job_failures, pending_claims, not_me_count.
    Side effects: none.
    """
    # Photo counts by status
    photo_stmt = (
        select(Photo.status, func.count(Photo.id))
        .where(Photo.event_id == event_id)
        .group_by(Photo.status)
    )
    photo_counts = dict(session.execute(photo_stmt).all())
    quarantined = int(photo_counts.get("quarantined", 0))
    approved = int(photo_counts.get("approved", 0))
    indexed = int(photo_counts.get("indexed", 0))
    failed = int(photo_counts.get("failed", 0))
    total = quarantined + approved + indexed + failed

    # Job failures
    job_stmt = select(func.count(Job.id)).where(Job.event_id == event_id, Job.status == "failed")
    job_failures = int(session.execute(job_stmt).scalar_one() or 0)

    # Pending claims
    claim_stmt = select(func.count(Claim.id)).where(
        Claim.event_id == event_id, Claim.status == "pending"
    )
    pending_claims = int(session.execute(claim_stmt).scalar_one() or 0)

    # Not-me cannot-link count
    cannot_stmt = select(func.count(CannotLink.id)).where(CannotLink.event_id == event_id)
    not_me_count = int(session.execute(cannot_stmt).scalar_one() or 0)

    return {
        "photos_quarantined": quarantined,
        "photos_approved": approved,
        "photos_indexed": indexed,
        "photos_failed": failed,
        "photos_total": total,
        "job_failures": job_failures,
        "pending_claims": pending_claims,
        "not_me_count": not_me_count,
    }
