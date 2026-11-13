"""HTTP routes for events, staff and invite links. Roles are checked on every request."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.core.rate_limit import RateLimiter
from app.core.request_checks import Ctx, PasskeyUser, User
from app.features.events import service
from app.features.events.schemas import (
    CameraInfo,
    CameraListResponse,
    CreateEventRequest,
    CreateInviteRequest,
    CreateSectionRequest,
    EventDetailResponse,
    EventListResponse,
    EventResponse,
    InviteKey,
    InviteListResponse,
    InviteResponse,
    PublicEventResponse,
    SectionListResponse,
    SectionResponse,
    SetCameraOffsetRequest,
    SetCameraOffsetResponse,
    SetSearchWindowRequest,
    SetSearchWindowResponse,
)

router = APIRouter(tags=["events"])
invite_limiter = RateLimiter("invite", limit=60, window_seconds=600)


def _response(view: service.EventView) -> EventResponse:
    """Purpose: service view -> API model."""
    return EventResponse(
        event_id=view.event_id,
        name=view.name,
        country=view.country,
        status=view.status,
        created_at=view.created_at,
        indexed_at=view.indexed_at,
        roles=list(view.roles),
        search_window_ends_at=view.search_window_ends_at,
    )


def _invite(view: service.InviteView) -> InviteResponse:
    """Purpose: service view -> API model."""
    return InviteResponse(
        invite_id=view.invite_id,
        created_at=view.created_at,
        expires_at=view.expires_at,
        revoked=view.revoked,
        url=view.url,
    )


@router.post("/events", status_code=201, response_model=EventResponse)
def create_event(body: CreateEventRequest, user: PasskeyUser, ctx: Ctx) -> EventResponse:
    """Purpose: create an event (allow-listed organisers, passkey session)."""
    return _response(service.create_event(ctx, user, body.name, body.country))


@router.get("/events", response_model=EventListResponse)
def my_events(user: User, ctx: Ctx) -> EventListResponse:
    """Purpose: the caller's events and roles."""
    return EventListResponse(events=[_response(v) for v in service.list_my_events(ctx, user)])


@router.get("/events/{event_id}", response_model=EventDetailResponse)
def get_event(event_id: UUID, user: User, ctx: Ctx) -> EventDetailResponse:
    """Purpose: one event (members only); counts for staff."""
    view, counts = service.get_event(ctx, user, event_id)
    return EventDetailResponse(**_response(view).model_dump(), counts=counts)


@router.post("/events/{event_id}/invites", status_code=201, response_model=InviteResponse)
def create_invite(
    event_id: UUID, body: CreateInviteRequest, user: PasskeyUser, ctx: Ctx
) -> InviteResponse:
    """Purpose: create a guest invite link (the URL is shown only now)."""
    return _invite(service.create_invite(ctx, user, event_id, body.days))


@router.get("/events/{event_id}/invites", response_model=InviteListResponse)
def list_invites(event_id: UUID, user: PasskeyUser, ctx: Ctx) -> InviteListResponse:
    """Purpose: the event's invite links (no secrets)."""
    return InviteListResponse(
        invites=[_invite(v) for v in service.list_invites(ctx, user, event_id)]
    )


@router.post("/events/{event_id}/invites/{invite_id}/revoke", status_code=204)
def revoke_invite(event_id: UUID, invite_id: UUID, user: PasskeyUser, ctx: Ctx) -> Response:
    """Purpose: revoke an invite link."""
    service.revoke_invite(ctx, user, event_id, invite_id)
    return Response(status_code=204)


@router.post(
    "/invites/{event_id}/resolve",
    response_model=PublicEventResponse,
    dependencies=[Depends(invite_limiter)],
)
def resolve_invite(event_id: UUID, body: InviteKey, ctx: Ctx) -> PublicEventResponse:
    """Purpose: landing data for an invite link (no sign-in needed)."""
    view = service.resolve_invite(ctx, event_id, body.key)
    return PublicEventResponse(event_id=view.event_id, name=view.name, status=view.status)


@router.post(
    "/invites/{event_id}/join", response_model=EventResponse, dependencies=[Depends(invite_limiter)]
)
def join(event_id: UUID, body: InviteKey, user: User, ctx: Ctx) -> EventResponse:
    """
    Purpose: a signed-in person becomes a guest of the event through its invite link.
    Signed-out visitors get 401 (accounts exist only after a passkey is verified).
    """
    return _response(service.join_with_invite(ctx, user, event_id, body.key))


@router.post("/events/{event_id}/sections", status_code=201, response_model=SectionResponse)
def create_section(
    event_id: UUID, body: CreateSectionRequest, user: PasskeyUser, ctx: Ctx
) -> SectionResponse:
    """Purpose: organiser creates an event section with optional time boundaries."""
    section, count = service.create_section(
        ctx, user, event_id, body.name, body.starts_at, body.ends_at
    )
    return SectionResponse(
        id=section.id,
        name=section.name,
        starts_at=section.starts_at,
        ends_at=section.ends_at,
        photo_count=count,
    )


@router.get("/events/{event_id}/sections", response_model=SectionListResponse)
def list_sections(event_id: UUID, user: User, ctx: Ctx) -> SectionListResponse:
    """Purpose: list sections for an event."""
    items = service.list_sections(ctx, user, event_id)
    return SectionListResponse(
        sections=[
            SectionResponse(
                id=sec.id,
                name=sec.name,
                starts_at=sec.starts_at,
                ends_at=sec.ends_at,
                photo_count=count,
            )
            for sec, count in items
        ]
    )


@router.get("/events/{event_id}/cameras", response_model=CameraListResponse)
def list_cameras(event_id: UUID, user: PasskeyUser, ctx: Ctx) -> CameraListResponse:
    """Purpose: list cameras detected in event photos with clock sync offsets."""
    cameras = service.list_cameras(ctx, user, event_id)
    return CameraListResponse(
        cameras=[
            CameraInfo(
                camera_make=c.camera_make,
                camera_model=c.camera_model,
                camera_serial=c.camera_serial,
                photo_count=c.photo_count,
                offset_seconds=c.offset_seconds,
            )
            for c in cameras
        ]
    )


@router.post("/events/{event_id}/cameras/offset", response_model=SetCameraOffsetResponse)
def set_camera_offset(
    event_id: UUID, body: SetCameraOffsetRequest, user: PasskeyUser, ctx: Ctx
) -> SetCameraOffsetResponse:
    """Purpose: set camera clock sync offset in seconds."""
    updated = service.set_camera_offset(
        ctx, user, event_id, body.camera_model, body.camera_serial, body.offset_seconds
    )
    return SetCameraOffsetResponse(
        camera_model=body.camera_model,
        camera_serial=body.camera_serial,
        offset_seconds=body.offset_seconds,
        updated_photos=updated,
    )


@router.post("/events/{event_id}/search-window", response_model=SetSearchWindowResponse)
def set_search_window(
    event_id: UUID, body: SetSearchWindowRequest, user: PasskeyUser, ctx: Ctx
) -> SetSearchWindowResponse:
    """Purpose: organiser sets search window end date."""
    ends_at = service.set_search_window(ctx, user, event_id, body.search_window_ends_at)
    return SetSearchWindowResponse(event_id=event_id, search_window_ends_at=ends_at)
