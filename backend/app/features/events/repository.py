"""Event queries. Callers run them inside an event- or admin-scoped transaction (RLS)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.events.models import Collection, Event, EventKey
from app.features.face_matching.models import Match
from app.features.photo_processing.models import Cluster, Face
from app.features.photo_upload.models import Photo


def get_event(session: Session, event_id: UUID) -> Event | None:
    """Purpose: one event (visible only in its own scope)."""
    return session.get(Event, event_id)


def list_events(session: Session) -> list[Event]:
    """Purpose: all events visible in the current scope, newest first."""
    return list(session.scalars(select(Event).order_by(Event.created_at.desc())))


def active_key(session: Session, event_id: UUID) -> EventKey | None:
    """Purpose: the newest key row of an event (destroyed or not)."""
    return session.scalars(
        select(EventKey).where(EventKey.event_id == event_id).order_by(EventKey.key_version.desc())
    ).first()


def counts(session: Session, event_id: UUID) -> dict[str, int]:
    """Purpose: photo, face and cluster counts of an event (no personal data)."""

    def count(model: type[Photo] | type[Face] | type[Cluster] | type[Collection]) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(model).where(model.event_id == event_id)
            )
            or 0
        )

    not_me_count = int(
        session.scalar(
            select(func.count(Match.id)).where(
                Match.event_id == event_id, Match.score_band == "not_me"
            )
        )
        or 0
    )

    return {
        "collections": count(Collection),
        "photos": count(Photo),
        "faces": count(Face),
        "clusters": count(Cluster),
        "not_me": not_me_count,
    }
