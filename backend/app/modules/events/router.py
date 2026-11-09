"""HTTP routes for events. TEMPORARY DEV-ONLY: protected by the dev admin token until login."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.context import AppContext
from app.core.security import require_dev_admin
from app.modules.events import service
from app.modules.events.schemas import (
    CreateEventRequest,
    EventDetailResponse,
    EventListResponse,
    EventResponse,
)

router = APIRouter(prefix="/events", tags=["events (dev token)"])
Actor = Annotated[str, Depends(require_dev_admin)]


def get_ctx(request: Request) -> AppContext:
    """Purpose: dependency returning the application context."""
    ctx: AppContext = request.app.state.ctx
    return ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


def _response(view: service.EventView) -> EventResponse:
    """Purpose: service view -> API model."""
    return EventResponse(
        event_id=view.event_id,
        name=view.name,
        country=view.country,
        status=view.status,
        created_at=view.created_at,
        indexed_at=view.indexed_at,
    )


@router.post("", status_code=201, response_model=EventResponse)
def create_event(body: CreateEventRequest, actor: Actor, ctx: Ctx) -> EventResponse:
    """Purpose: create an event (and its data key)."""
    return _response(service.create_event(ctx, body.name, body.country, actor))


@router.get("", response_model=EventListResponse)
def list_events(actor: Actor, ctx: Ctx) -> EventListResponse:
    """Purpose: list events (metadata only)."""
    return EventListResponse(events=[_response(v) for v in service.list_events(ctx, actor)])


@router.get("/{event_id}", response_model=EventDetailResponse)
def get_event(event_id: UUID, _actor: Actor, ctx: Ctx) -> EventDetailResponse:
    """Purpose: one event with photo, face and cluster counts."""
    view, counts = service.get_event(ctx, event_id)
    return EventDetailResponse(**_response(view).model_dump(), counts=counts)
