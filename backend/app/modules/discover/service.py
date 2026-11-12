"""Discover (milestone 7): one live scan searches every searchable event; one gallery shows
all of the person's photos.

Owner decision (12 Nov 2026): a signed-in person's scan covers all events. Each event is still
searched on its own: its own transaction, row-level security scope and data key, and the same
release rules as the per-event search. Nothing is ever compared across events.

Order (cheapest first, nothing biometric before the checks pass):
1. guest search allowed and the frame count is valid;
2. the person agreed to the current notice (version sent with the scan);
3. per event: skip events where the person is staff (invariant 5); otherwise make them a guest,
   record consent if missing, then run the per-event access check and hourly limits;
4. liveness + embedding once, in memory;
5. per event: match and release. An event being re-indexed, or whose data key cannot be
   opened, is skipped, not failed.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.core.auth import CurrentUser
from app.core.context import AppContext, guest_search_allowed
from app.core.deps import STAFF_ROLES, roles_in_event
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.db.event_scope import ADMIN_SCOPE, event_transaction, scoped_transaction
from app.modules.audit.service import record
from app.modules.consent import service as consent_service
from app.modules.events.models import Event, EventMember
from app.modules.events.service import EventKeyDestroyedError, list_my_events
from app.modules.gallery.service import GalleryItem, get_gallery_content
from app.modules.matching.service import (
    _check_and_count,
    embed_selfie_frames,
    release_in_event,
)
from app.ports.key_manager import KeyUnwrapError

logger = logging.getLogger("facescan.discover")


@dataclass(frozen=True, slots=True)
class DiscoverResult:
    """How many photos were released and in how many events. No ids of other events' data."""

    released: int
    events_with_photos: int
    events_searched: int


@dataclass(frozen=True, slots=True)
class EventPhotos:
    """The person's released photos in one event."""

    event_id: UUID
    event_name: str
    photos: list[GalleryItem]


def searchable_event_ids(ctx: AppContext) -> list[UUID]:
    """
    Purpose: ids of events whose photos are indexed and whose search window is open.
    Inputs: ctx. Outputs: event ids, newest first.
    Side effects: one read in the admin scope (event metadata only, no photos or faces).
    """
    now = datetime.now(UTC)
    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as session:
        rows = session.execute(
            select(Event.id, Event.search_window_ends_at)
            .where(Event.status == "searchable")
            .order_by(Event.created_at.desc())
        ).all()
    return [event_id for event_id, ends in rows if ends is None or ends >= now]


def _enrol(ctx: AppContext, user: CurrentUser, event_id: UUID, notice_version: str) -> bool:
    """
    Purpose: make the person a guest of one event and record consent when missing.
    Inputs: ctx, user, event_id, the notice version they agreed to.
    Outputs: False when the person is staff of this event (never searched), else True.
    Side effects: may insert event_members (guest) and consent rows; audit rows.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        roles = roles_in_event(session, event_id, user.user_id)
        if roles & set(STAFF_ROLES):
            return False
        if "guest" not in roles:
            session.add(EventMember(event_id=event_id, user_id=user.user_id, role="guest"))
            record(session, event_id=event_id, actor=user.actor, action="discover.joined")
        consent = consent_service.active_consent(
            session, event_id, user.user_id, consent_service.FACE_SEARCH
        )
        consent_current = consent is not None and consent.notice_version == notice_version
    if not consent_current:
        consent_service.grant(ctx, user, event_id, notice_version, "en")
    return True


def look_scan_allowed(ctx: AppContext) -> bool:
    """
    Purpose: the "look" scan (no blink challenge) exists only while the owner compares scan
        methods in development (FACESCAN_ENV=dev). Inputs: ctx. Outputs: bool.
    Side effects: none.
    """
    return ctx.settings.env == "dev"


def search_all_events(
    ctx: AppContext,
    user: CurrentUser,
    frames: list[bytes],
    notice_version: str,
    require_blink: bool = True,
) -> DiscoverResult:
    """
    Purpose: search every searchable event with one live scan (selfie + blink).
    Inputs: ctx, signed-in user, 3 to FACESCAN_DISCOVER_MAX_FRAMES encoded frames (front,
        blink burst, front; memory only), agreed notice version, require_blink (False is
        refused outside development).
    Outputs: DiscoverResult (counts only).
    Raises 403 when search is disabled, 409 when the notice changed, 422 for bad frames or
        failed liveness, 429 over the hourly limits.
    Side effects: event memberships, consents, search attempts, matches and audit rows in
        each event; frames are cleared after embedding and never stored.
    """
    settings = ctx.settings
    if not guest_search_allowed(settings):
        frames.clear()
        raise HTTPException(status_code=403, detail="search_disabled_without_liveness")
    if not 3 <= len(frames) <= settings.discover_max_frames:
        frames.clear()
        raise UnprocessableError(f"send 3 to {settings.discover_max_frames} selfie frames")
    if not require_blink and not look_scan_allowed(ctx):
        frames.clear()
        raise HTTPException(status_code=403, detail="look_scan_disabled")
    if notice_version != consent_service.notice_for(ctx).version:
        frames.clear()
        raise ConflictError("the notice has changed; read it again")

    eligible: list[UUID] = []
    try:
        for event_id in searchable_event_ids(ctx):
            if _enrol(ctx, user, event_id, notice_version):
                _check_and_count(ctx, user, event_id)
                eligible.append(event_id)
    except Exception:
        frames.clear()
        raise
    if not eligible:
        frames.clear()
        return DiscoverResult(released=0, events_with_photos=0, events_searched=0)

    # Liveness sees every frame (the blink burst); matching uses the open-eye first and last.
    last = len(frames) - 1
    queries = embed_selfie_frames(
        ctx, frames, front_only=True, embed_indices=(0, last), require_blink=require_blink
    )
    released = 0
    events_with_photos = 0
    searched = 0
    for event_id in eligible:
        try:
            with event_transaction(ctx.sessions, event_id) as session:
                result = release_in_event(session, ctx, user, event_id, queries, True)
        except ConflictError:
            continue  # this event is being re-indexed; the next scan will cover it
        except (KeyUnwrapError, EventKeyDestroyedError, NotFoundError):
            # One broken event must not stop the search of all the others.
            logger.warning("discover skipped event %s: its data key is unusable", event_id)
            continue
        searched += 1
        released += len(result.release)
        events_with_photos += 1 if result.release else 0
    queries.clear()
    return DiscoverResult(
        released=released, events_with_photos=events_with_photos, events_searched=searched
    )


def my_photos(ctx: AppContext, user: CurrentUser) -> list[EventPhotos]:
    """
    Purpose: the person's released photos in every event they are a guest of.
    Inputs: ctx, user. Outputs: one EventPhotos per event that has photos, newest event first.
    Side effects: gallery.viewed audit rows (one per event read).
    """
    groups: list[EventPhotos] = []
    for event in list_my_events(ctx, user):
        if "guest" not in event.roles:
            continue
        try:
            content = get_gallery_content(ctx, user, event.event_id)
        except HTTPException as exc:
            if exc.status_code in (403, 404):
                continue  # consent withdrawn or membership gone: nothing to show
            raise
        if content.photos:
            groups.append(EventPhotos(event.event_id, event.name, content.photos))
    return groups
