"""API models for events, staff and invites (no personal data, no vectors)."""

from datetime import datetime
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
    search_window_ends_at: datetime | None = None


class EventDetailResponse(EventResponse):
    """An event with counts (staff only; null for guests)."""

    counts: dict[str, int] | None


class EventListResponse(BaseModel):
    """The caller's events."""

    events: list[EventResponse]


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


class CreateSectionRequest(BaseModel):
    """Body: create an event section with optional time boundary."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class SectionResponse(BaseModel):
    """Section metadata and assigned photo count."""

    id: UUID
    name: str
    starts_at: datetime | None
    ends_at: datetime | None
    photo_count: int = 0


class SectionListResponse(BaseModel):
    """List of sections for an event."""

    sections: list[SectionResponse]


class CameraInfo(BaseModel):
    """Camera detected from photo EXIF with offset and photo count."""

    camera_make: str | None = None
    camera_model: str
    camera_serial: str | None = None
    photo_count: int
    offset_seconds: int


class CameraListResponse(BaseModel):
    """List of cameras detected in an event."""

    cameras: list[CameraInfo]


class SetCameraOffsetRequest(BaseModel):
    """Body: set clock sync offset in seconds for a camera model / serial."""

    model_config = ConfigDict(extra="forbid")
    camera_model: str = Field(min_length=1, max_length=64)
    camera_serial: str | None = Field(default=None, max_length=64)
    offset_seconds: int = Field(ge=-86400, le=86400)


class SetCameraOffsetResponse(BaseModel):
    """Result of setting camera clock sync offset."""

    camera_model: str
    camera_serial: str | None
    offset_seconds: int
    updated_photos: int


class SetSearchWindowRequest(BaseModel):
    """Body: set search window end date."""

    model_config = ConfigDict(extra="forbid")
    search_window_ends_at: datetime | None = None


class SetSearchWindowResponse(BaseModel):
    """Response after updating search window."""

    event_id: UUID
    search_window_ends_at: datetime | None
