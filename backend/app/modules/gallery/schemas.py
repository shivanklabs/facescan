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


class ClaimNotice(BaseModel):
    """Notice of a reviewed claim for the guest."""

    claim_id: UUID
    photo_id: UUID
    status: str
    reason: str
    reviewed_at: datetime | None


class GalleryResponse(BaseModel):
    """The guest's released photos and claim notices in one event."""

    photos: list[GalleryPhoto]
    rejections: list[ClaimNotice] = []
