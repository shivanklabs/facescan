"""API models for uploads and approval."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UploadResponse(BaseModel):
    """Stored photos (random ids), duplicate status, and their collection."""

    collection_id: UUID
    photo_ids: list[UUID]
    already_uploaded: list[int] = []
    message: str | None = None


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


class UploadInitRequest(BaseModel):
    """Initiates a resumable chunked upload session."""

    model_config = ConfigDict(extra="forbid")
    collection_id: UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    total_chunks: int = Field(ge=1, le=10000)
    expected_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")


class UploadInitResponse(BaseModel):
    """Initial session response with chunk parameters."""

    upload_id: UUID
    collection_id: UUID
    chunk_size: int
    total_chunks: int
    received_chunks: list[int]


class UploadChunkResponse(BaseModel):
    """Response after a chunk is received and verified."""

    received_chunks: list[int]
    complete: bool


class UploadStatusResponse(BaseModel):
    """Status query response for resuming interrupted uploads."""

    upload_id: UUID
    filename: str
    byte_size: int
    total_chunks: int
    received_chunks: list[int]
    status: str


class UploadCompleteResponse(BaseModel):
    """Response after full assembly and ingestion."""

    collection_id: UUID
    photo_id: UUID | None = None
    status: str
    already_uploaded: bool = False
    message: str | None = None


class FailedPhotoInfo(BaseModel):
    """Metadata for a photo that failed processing."""

    id: UUID
    filename: str
    created_at: datetime


class ProcessingStatusResponse(BaseModel):
    """Photographer status dashboard counts and failed photo list."""

    quarantined: int
    approved: int
    indexed: int
    failed: int
    near_duplicates: int
    failed_photos: list[FailedPhotoInfo]


class RetryPhotoResponse(BaseModel):
    """Result of retrying a failed photo."""

    photo_id: UUID
    status: str
    message: str
