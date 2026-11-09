"""API models for the guest gallery (signed URLs only; no scores, no other guests)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GalleryPhoto(BaseModel):
    """One of the guest's released photos."""

    photo_id: UUID
    url: str
    expires_at: datetime
    download_path: str


class GalleryResponse(BaseModel):
    """The guest's released photos in one event."""

    photos: list[GalleryPhoto]
