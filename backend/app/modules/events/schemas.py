"""API models for events, staff and invites (no personal data, no vectors)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateEventRequest(BaseModel):
    """Body: create an event."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")


class EventResponse(BaseModel):
    """An event's metadata and the caller's roles in it."""

    event_id: UUID
    name: str
    country: str
    status: str
    created_at: datetime
    indexed_at: datetime | None
    roles: list[str]


class EventDetailResponse(EventResponse):
    """An event with counts (staff only; null for guests)."""

    counts: dict[str, int] | None


class EventListResponse(BaseModel):
    """The caller's events."""

    events: list[EventResponse]


class AddStaffRequest(BaseModel):
    """Body: add a photographer or co-organiser by email."""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    role: Literal["organiser", "photographer"]


class CreateInviteRequest(BaseModel):
    """Body: create an invite link."""

    model_config = ConfigDict(extra="forbid")
    days: int | None = Field(default=None, ge=1, le=90)


class InviteResponse(BaseModel):
    """An invite link. url (with the secret) is returned only when it is created."""

    invite_id: UUID
    created_at: datetime
    expires_at: datetime
    revoked: bool
    url: str | None = None


class InviteListResponse(BaseModel):
    """An event's invite links (no secrets)."""

    invites: list[InviteResponse]


class InviteKey(BaseModel):
    """Body: the secret from an invite link."""

    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=20, max_length=100)


class PublicEventResponse(BaseModel):
    """What an invite link shows before sign-in."""

    event_id: UUID
    name: str
    status: str
