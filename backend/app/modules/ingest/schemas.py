"""API models for uploads and approval."""

from uuid import UUID

from pydantic import BaseModel


class UploadResponse(BaseModel):
    """Stored photos (random ids) and their collection."""

    collection_id: UUID
    photo_ids: list[UUID]


class ApproveResponse(BaseModel):
    """Approval result; indexing runs in the worker."""

    collection_id: UUID
    approved_photos: int
    status: str


class CollectionResponse(BaseModel):
    """A collection for the approval page."""

    collection_id: UUID
    status: str
    photos: int
    previews: list[str]


class CollectionListResponse(BaseModel):
    """An event's collections."""

    collections: list[CollectionResponse]
