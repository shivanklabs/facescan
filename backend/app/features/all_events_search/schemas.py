"""API models for discover: counts and the person's own photos only (no vectors or scores)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DiscoverSearchResponse(BaseModel):
    """Result of one scan across all events."""

    released: int
    events_with_photos: int
    events_searched: int


class DiscoverPhoto(BaseModel):
    """One released photo: a short-lived signed web-size URL and the original download path."""

    photo_id: UUID
    url: str
    expires_at: datetime
    download_path: str


class DiscoverEvent(BaseModel):
    """The person's photos in one event."""

    event_id: UUID
    event_name: str
    photos: list[DiscoverPhoto]


class DiscoverPhotosResponse(BaseModel):
    """All of the person's photos, grouped by event."""

    events: list[DiscoverEvent]
