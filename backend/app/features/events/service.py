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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.encryption import new_data_key
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.login_sessions import (
    CurrentUser,
    create_anonymous_user,
    create_session,
    find_or_create_user,
    hash_token,
    new_token,
    normalise_email,
)
from app.core.login_sessions import organiser_allowed as is_organiser_allowed
from app.core.request_checks import STAFF_ROLES, require_role
from app.core.services_setup import AppContext
from app.db.event_isolation import event_transaction, user_transaction
from app.features.activity_log.service import record
from app.features.events import repository
from app.features.events.models import (
    CameraOffset,
    Event,
    EventInvite,
    EventKey,
    EventMember,
    EventSection,
)
from app.features.login.models import User
from app.features.photo_upload.models import Photo
from app.interfaces.encryption_keys import KeyManager


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
    search_window_ends_at: datetime | None = None


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
        event.id,
        event.name,
        event.country,
        event.status,
        event.created_at,
        event.indexed_at,
        roles,
        event.search_window_ends_at,
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


def join_with_invite(
    ctx: AppContext, user: CurrentUser | None, event_id: UUID, secret: str
) -> tuple[EventView, str | None]:
    """
    Purpose: make the caller a guest of the event through a valid invite link. If caller is
        not signed in, creates an anonymous guest account and a session token.
    Inputs: ctx, user (CurrentUser | None), event_id, secret.
    Outputs: tuple of (EventView, session_token or None).
    Side effects: inserts user/session (if anonymous), inserts event_members (guest), records audit.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        event = repository.get_event(session, event_id)
        if (
            event is None
            or event.status == "closed"
            or _valid_invite(session, event_id, secret) is None
        ):
            raise NotFoundError("invite link is invalid or has expired")

        session_token: str | None = None
        if user is None:
            anon_user = create_anonymous_user(session, ctx.keys)
            session_token = create_session(
                session, ctx.settings, anon_user.id, "magic_link", event_id
            )
            session.add(EventMember(event_id=event_id, user_id=anon_user.id, role="guest"))
            record(session, event_id=event_id, actor=f"user:{anon_user.id}", action="invite.joined")
            return _view(event, ("guest",)), session_token

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
        return _view(event, tuple(sorted(roles | {"guest"}))), None


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


def create_section(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    name: str,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> tuple[EventSection, int]:
    """
    Purpose: create an event section (name, optional time boundaries) and auto-assign photos.
    Inputs: event_id, name, starts_at, ends_at, user.
    Outputs: tuple of (EventSection, assigned photo count).
    Side effects: inserts event_sections row; updates photo.section_id; writes audit row.
    """
    if starts_at and ends_at and starts_at > ends_at:
        raise UnprocessableError("starts_at must be earlier than ends_at")

    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        event = repository.get_event(session, event_id)
        if event is None or event.status == "closed":
            raise NotFoundError("event not found or closed")

        section = EventSection(
            event_id=event_id,
            name=name,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        session.add(section)
        session.flush()

        assigned_count = 0
        if starts_at and ends_at:
            photos = session.scalars(
                select(Photo).where(
                    Photo.event_id == event_id,
                    Photo.corrected_time >= starts_at,
                    Photo.corrected_time <= ends_at,
                )
            ).all()
            for photo in photos:
                photo.section_id = section.id
                assigned_count += 1

        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="section.created",
            target=str(section.id),
            details={"name": name, "assigned_photos": assigned_count},
        )
        return section, assigned_count


def list_sections(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
) -> list[tuple[EventSection, int]]:
    """
    Purpose: list all sections for an event with assigned photo counts.
    Inputs: event_id, user.
    Outputs: list of (EventSection, photo_count) tuples.
    Side effects: none.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser", "photographer", "guest"))
        sections = list(
            session.scalars(
                select(EventSection)
                .where(EventSection.event_id == event_id)
                .order_by(EventSection.starts_at.asc().nulls_last(), EventSection.created_at.asc())
            )
        )
        counts = dict(
            session.execute(
                select(Photo.section_id, func.count(Photo.id))
                .where(Photo.event_id == event_id, Photo.section_id.is_not(None))
                .group_by(Photo.section_id)
            ).all()
        )
        return [(sec, counts.get(sec.id, 0)) for sec in sections]


@dataclass(frozen=True, slots=True)
class CameraView:
    """Camera detected from photo EXIF with clock sync offset and photo count."""

    camera_make: str | None
    camera_model: str
    camera_serial: str | None
    photo_count: int
    offset_seconds: int


def list_cameras(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
) -> list[CameraView]:
    """
    Purpose: list distinct cameras detected from photo EXIF in this event along with offsets.
    Inputs: event_id, user.
    Outputs: list of CameraView objects with make, model, serial, photo_count, offset_seconds.
    Side effects: none.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser", "photographer"))
        # Group photos by camera
        rows = session.execute(
            select(
                Photo.camera_make,
                Photo.camera_model,
                Photo.camera_serial,
                func.count(Photo.id),
            )
            .where(Photo.event_id == event_id, Photo.camera_model.is_not(None))
            .group_by(Photo.camera_make, Photo.camera_model, Photo.camera_serial)
        ).all()

        offsets = list(
            session.scalars(select(CameraOffset).where(CameraOffset.event_id == event_id))
        )
        result: list[CameraView] = []
        for make, model, serial, count in rows:
            offset_sec = 0
            for off in offsets:
                if off.camera_model == model and (
                    off.camera_serial is None or off.camera_serial == serial
                ):
                    offset_sec = off.offset_seconds
                    break
            result.append(
                CameraView(
                    camera_make=str(make) if make is not None else None,
                    camera_model=str(model),
                    camera_serial=str(serial) if serial is not None else None,
                    photo_count=int(count),
                    offset_seconds=offset_sec,
                )
            )
        return result


def set_camera_offset(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    camera_model: str,
    camera_serial: str | None,
    offset_seconds: int,
) -> int:
    """
    Purpose: upsert clock offset for camera and recompute photo corrected_time and sections.
    Inputs: event_id, camera_model, camera_serial, offset_seconds, user.
    Outputs: count of updated photos.
    Side effects: upserts camera_offsets; updates photos; writes audit row.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser", "photographer"))
        # Upsert camera_offsets
        existing = session.scalars(
            select(CameraOffset).where(
                CameraOffset.event_id == event_id,
                CameraOffset.camera_model == camera_model,
                CameraOffset.camera_serial == camera_serial,
            )
        ).first()
        if existing is not None:
            existing.offset_seconds = offset_seconds
        else:
            session.add(
                CameraOffset(
                    event_id=event_id,
                    camera_model=camera_model,
                    camera_serial=camera_serial,
                    offset_seconds=offset_seconds,
                )
            )
        session.flush()

        sections = list(
            session.scalars(select(EventSection).where(EventSection.event_id == event_id))
        )

        stmt = select(Photo).where(
            Photo.event_id == event_id,
            Photo.camera_model == camera_model,
        )
        if camera_serial is not None:
            stmt = stmt.where(Photo.camera_serial == camera_serial)

        photos = session.scalars(stmt).all()
        updated_count = 0
        for photo in photos:
            if photo.taken_at is not None:
                photo.corrected_time = photo.taken_at + timedelta(seconds=offset_seconds)
            else:
                photo.corrected_time = photo.created_at

            # Re-evaluate section assignment
            matched_section_id: UUID | None = None
            for s in sections:
                if s.starts_at and s.ends_at and s.starts_at <= photo.corrected_time <= s.ends_at:
                    matched_section_id = s.id
                    break
            photo.section_id = matched_section_id
            updated_count += 1

        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="camera.offset_updated",
            details={
                "camera_model": camera_model,
                "camera_serial": camera_serial,
                "offset_seconds": offset_seconds,
                "updated_photos": updated_count,
            },
        )
        return updated_count


def set_search_window(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    search_window_ends_at: datetime | None,
) -> datetime | None:
    """
    Purpose: set search window end date on an event.
    Inputs: event_id, search_window_ends_at (datetime or None), user.
    Outputs: updated search_window_ends_at.
    Side effects: updates events.search_window_ends_at; writes audit row.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        event = repository.get_event(session, event_id)
        if event is None or event.status == "closed":
            raise NotFoundError("event not found or closed")
        event.search_window_ends_at = search_window_ends_at
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="event.search_window_updated",
            details={
                "search_window_ends_at": (
                    search_window_ends_at.isoformat() if search_window_ends_at else None
                )
            },
        )
        return event.search_window_ends_at
