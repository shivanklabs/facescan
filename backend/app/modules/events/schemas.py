"""API models for events (no personal data, no vectors)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateEventRequest(BaseModel):
    """Body: create an event."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")


class EventResponse(BaseModel):
    """An event's metadata."""

    event_id: UUID
    name: str
    country: str
    status: str
    created_at: datetime
    indexed_at: datetime | None


class EventDetailResponse(EventResponse):
    """An event with counts."""

    counts: dict[str, int]


class EventListResponse(BaseModel):
    """All events (dev admin view)."""

    events: list[EventResponse]
