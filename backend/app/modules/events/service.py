"""Event lifecycle and per-event data keys.

Each event gets a random 32-byte data key, stored only wrapped by the master key through the
KeyManager port. Faces, prototypes and calibration are encrypted with it (app/core/crypto.py).
Destroying the key (wrapped_key set to NULL) makes all of them unreadable for good.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.context import AppContext
from app.core.crypto import new_data_key
from app.core.errors import ConflictError, NotFoundError
from app.db.event_scope import ADMIN_SCOPE, event_transaction, scoped_transaction
from app.modules.audit.service import record
from app.modules.events import repository
from app.modules.events.models import Event, EventKey
from app.ports.key_manager import KeyManager


class EventKeyDestroyedError(Exception):
    """The event's data key was destroyed; its biometric data can never be read again."""


@dataclass(frozen=True, slots=True)
class EventView:
    """Event metadata returned by the API (no personal data)."""

    event_id: UUID
    name: str
    country: str
    status: str
    created_at: datetime
    indexed_at: datetime | None


def _view(event: Event) -> EventView:
    """Purpose: ORM row -> API view."""
    return EventView(
        event.id, event.name, event.country, event.status, event.created_at, event.indexed_at
    )


def create_event(ctx: AppContext, name: str, country: str, actor: str) -> EventView:
    """
    Purpose: create an event and its wrapped data key in one transaction.
    Side effects: inserts events, event_keys and audit_log rows.
    """
    event_id = uuid.uuid4()
    with event_transaction(ctx.sessions, event_id) as session:
        event = Event(id=event_id, event_id=event_id, name=name, country=country.upper())
        session.add(event)
        session.flush()
        session.add(
            EventKey(
                event_id=event_id,
                key_version=1,
                wrapped_key=ctx.keys.wrap(event_id, new_data_key()),
                master_key_id=ctx.keys.master_key_id,
            )
        )
        record(
            session, event_id=event_id, actor=actor, action="event.created", target=str(event_id)
        )
        session.flush()
        session.refresh(event)
        return _view(event)


def list_events(ctx: AppContext, actor: str) -> list[EventView]:
    """
    Purpose: list event metadata (admin scope: events table only, read-only).
    Side effects: writes one audit row without an event.
    """
    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as session:
        events = [_view(e) for e in repository.list_events(session)]
        record(
            session,
            event_id=None,
            actor=actor,
            action="event.listed",
            details={"count": len(events)},
        )
        return events


def get_event(ctx: AppContext, event_id: UUID) -> tuple[EventView, dict[str, int]]:
    """Purpose: one event with counts. Raises NotFoundError."""
    with event_transaction(ctx.sessions, event_id) as session:
        event = repository.get_event(session, event_id)
        if event is None:
            raise NotFoundError("event not found")
        return _view(event), repository.counts(session, event_id)


def data_key(session: Session, keys: KeyManager, event_id: UUID) -> tuple[bytes, int]:
    """
    Purpose: unwrap the event's current data key inside an event-scoped transaction.
    Outputs: (key, key_version). Raises EventKeyDestroyedError or NotFoundError.
    """
    row = repository.active_key(session, event_id)
    if row is None:
        raise NotFoundError("event key not found")
    if row.wrapped_key is None:
        raise EventKeyDestroyedError("event data key was destroyed")
    return keys.unwrap(event_id, row.wrapped_key), row.key_version


def destroy_event_key(ctx: AppContext, event_id: UUID, actor: str) -> None:
    """
    Purpose: crypto-shred an event: delete the wrapped key so its faces, prototypes and
        calibration can never be decrypted again. Irreversible.
    Side effects: updates event_keys; closes the event; writes an audit row.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        event = repository.get_event(session, event_id)
        row = repository.active_key(session, event_id)
        if event is None or row is None:
            raise NotFoundError("event not found")
        if row.wrapped_key is None:
            raise ConflictError("event key already destroyed")
        row.wrapped_key = None
        row.destroyed_at = datetime.now(UTC)
        event.status = "closed"
        record(session, event_id=event_id, actor=actor, action="event.key_destroyed")
