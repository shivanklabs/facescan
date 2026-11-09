"""Events: lifecycle, staff membership, invite links (mode A) and per-event data keys.

Each event gets a random 32-byte data key, stored only wrapped by the master key through the
KeyManager port. Faces, prototypes, membership and calibration are encrypted with it.
Destroying the key (wrapped_key set to NULL) makes all of them unreadable for good.

Invite links carry the event id and a random secret; only the secret's SHA-256 is stored.
Organisers create, list and revoke them; they expire after FACESCAN_INVITE_DEFAULT_DAYS.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, find_or_create_user, hash_token, new_token, normalise_email
from app.core.auth import organiser_allowed as is_organiser_allowed
from app.core.context import AppContext
from app.core.crypto import new_data_key
from app.core.deps import STAFF_ROLES, require_role
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.db.event_scope import event_transaction, user_transaction
from app.modules.audit.service import record
from app.modules.events import repository
from app.modules.events.models import Event, EventInvite, EventKey, EventMember
from app.modules.identity.models import User
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
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InviteView:
    """An invite link (the secret only once, at creation)."""

    invite_id: UUID
    created_at: datetime
    expires_at: datetime
    revoked: bool
    url: str | None = None


def _view(event: Event, roles: tuple[str, ...] = ()) -> EventView:
    """Purpose: ORM row -> view."""
    return EventView(
        event.id, event.name, event.country, event.status, event.created_at, event.indexed_at, roles
    )


def create_event(ctx: AppContext, user: CurrentUser, name: str, country: str) -> EventView:
    """
    Purpose: create an event, its wrapped data key and the creator's organiser membership.
    Raises 403 unless the user is on the organiser allowlist.
    Side effects: inserts events, event_keys, event_members, audit_log rows.
    """
    with ctx.sessions() as session:
        account = session.get(User, user.user_id)
        if account is None or not is_organiser_allowed(ctx.settings, ctx.keys, account):
            raise HTTPException(status_code=403, detail="not_an_organiser")
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
        session.add(EventMember(event_id=event_id, user_id=user.user_id, role="organiser"))
        record(session, event_id=event_id, actor=user.actor, action="event.created")
        session.flush()
        session.refresh(event)
        return _view(event, ("organiser",))


def list_my_events(ctx: AppContext, user: CurrentUser) -> list[EventView]:
    """Purpose: events the user belongs to, with their roles (user scope, read-only)."""
    with user_transaction(ctx.sessions, user.user_id) as session:
        roles: dict[UUID, list[str]] = {}
        for m in session.scalars(select(EventMember).where(EventMember.user_id == user.user_id)):
            roles.setdefault(m.event_id, []).append(m.role)
        events = session.scalars(select(Event).where(Event.id.in_(roles))).all()
        return [
            _view(e, tuple(sorted(roles[e.id])))
            for e in sorted(events, key=lambda e: e.created_at, reverse=True)
        ]


def get_event(
    ctx: AppContext, user: CurrentUser, event_id: UUID
) -> tuple[EventView, dict[str, int] | None]:
    """Purpose: one event for a member; counts for staff only. 404 for non-members."""
    with event_transaction(ctx.sessions, event_id) as session:
        roles = require_role(
            session, event_id, user.user_id, ("organiser", "photographer", "guest")
        )
        event = repository.get_event(session, event_id)
        if event is None:
            raise NotFoundError("event not found")
        counts = repository.counts(session, event_id) if roles & set(STAFF_ROLES) else None
        return _view(event, tuple(sorted(roles))), counts


def add_staff(ctx: AppContext, user: CurrentUser, event_id: UUID, email: str, role: str) -> None:
    """
    Purpose: organiser adds a photographer or co-organiser by email (account created if new;
        they sign in with a magic link and must then register a passkey).
    Side effects: may insert users; inserts event_members and audit rows.
    """
    if role not in STAFF_ROLES:
        raise UnprocessableError("role must be organiser or photographer")
    try:
        address = normalise_email(email)
    except ValueError as exc:
        raise UnprocessableError("enter a valid email address") from exc
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        member = find_or_create_user(session, ctx.keys, address)
        exists = session.scalars(
            select(EventMember).where(
                EventMember.event_id == event_id,
                EventMember.user_id == member.id,
                EventMember.role == role,
            )
        ).first()
        if exists is None:
            session.add(EventMember(event_id=event_id, user_id=member.id, role=role))
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="member.added",
            target=f"user:{member.id}",
            details={"role": role},
        )


def create_invite(
    ctx: AppContext, user: CurrentUser, event_id: UUID, days: int | None
) -> InviteView:
    """Purpose: a new random, revocable, expiring guest invite link (secret shown once)."""
    secret = new_token()
    expires = datetime.now(UTC) + timedelta(days=days or ctx.settings.invite_default_days)
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        invite = EventInvite(
            event_id=event_id,
            secret_hash=hash_token(secret),
            created_by=user.user_id,
            expires_at=expires,
        )
        session.add(invite)
        session.flush()
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="invite.created",
            target=str(invite.id),
        )
        url = f"{ctx.settings.auth_rp_origin.rstrip('/')}/e/{event_id}?k={secret}"
        return InviteView(invite.id, datetime.now(UTC), expires, False, url)


def list_invites(ctx: AppContext, user: CurrentUser, event_id: UUID) -> list[InviteView]:
    """Purpose: an event's invite links (without secrets)."""
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        rows = session.scalars(
            select(EventInvite)
            .where(EventInvite.event_id == event_id)
            .order_by(EventInvite.created_at.desc())
        )
        return [
            InviteView(i.id, i.created_at, i.expires_at, i.revoked_at is not None) for i in rows
        ]


def revoke_invite(ctx: AppContext, user: CurrentUser, event_id: UUID, invite_id: UUID) -> None:
    """Purpose: revoke an invite link (it stops working at once)."""
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        invite = session.get(EventInvite, invite_id)
        if invite is None or invite.event_id != event_id:
            raise NotFoundError("invite not found")
        if invite.revoked_at is None:
            invite.revoked_at = datetime.now(UTC)
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="invite.revoked",
            target=str(invite_id),
        )


def _valid_invite(session: Session, event_id: UUID, secret: str) -> EventInvite | None:
    """Purpose: the live invite matching a secret (inside the event's transaction)."""
    if not secret or len(secret) > 100:
        return None
    invite = session.scalars(
        select(EventInvite).where(
            EventInvite.event_id == event_id, EventInvite.secret_hash == hash_token(secret)
        )
    ).first()
    if invite is None or invite.revoked_at is not None or invite.expires_at <= datetime.now(UTC):
        return None
    return invite


def resolve_invite(ctx: AppContext, event_id: UUID, secret: str) -> EventView:
    """Purpose: public landing data for a valid invite link. 404 for anything invalid."""
    with event_transaction(ctx.sessions, event_id) as session:
        event = repository.get_event(session, event_id)
        if (
            event is None
            or event.status == "closed"
            or _valid_invite(session, event_id, secret) is None
        ):
            raise NotFoundError("invite link is invalid or has expired")
        return _view(event)


def join_with_invite(ctx: AppContext, user: CurrentUser, event_id: UUID, secret: str) -> EventView:
    """
    Purpose: make the signed-in user a guest of the event through a valid invite link.
    Side effects: inserts event_members (guest) and an audit row.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        event = repository.get_event(session, event_id)
        if (
            event is None
            or event.status == "closed"
            or _valid_invite(session, event_id, secret) is None
        ):
            raise NotFoundError("invite link is invalid or has expired")
        roles = {
            m.role
            for m in session.scalars(
                select(EventMember).where(
                    EventMember.event_id == event_id, EventMember.user_id == user.user_id
                )
            )
        }
        if "guest" not in roles:
            session.add(EventMember(event_id=event_id, user_id=user.user_id, role="guest"))
            record(session, event_id=event_id, actor=user.actor, action="invite.joined")
        return _view(event, tuple(sorted(roles | {"guest"})))


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
    Purpose: crypto-shred an event: delete the wrapped key so its faces, prototypes,
        membership and calibration can never be decrypted again. Irreversible.
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
