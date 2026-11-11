"""Pydantic schemas for scene search, candidate tracking, and 1:1 claims."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SceneSearchRequest(BaseModel):
    """Guest scene search request payload."""

    query: str = Field(min_length=2, max_length=256, description="Natural language search query")
    section_id: UUID | None = Field(default=None, description="Optional section filter")
    time_from: datetime | None = Field(default=None, description="Optional start time filter")
    time_to: datetime | None = Field(default=None, description="Optional end time filter")


class CandidateFace(BaseModel):
    """One face detected in a candidate photo."""

    face_id: UUID
    bbox: tuple[float, float, float, float]  # (x, y, w, h)


class SceneCandidateItem(BaseModel):
    """One candidate photo returned by scene search with blurred preview link and faces."""

    photo_id: UUID
    preview_url: str
    faces: list[CandidateFace]


class SceneSearchResponse(BaseModel):
    """Results of scene search."""

    candidates: list[SceneCandidateItem]
    query_rewritten: bool


class SubmitClaimRequest(BaseModel):
    """Payload for submitting a 1:1 face claim on a candidate face."""

    photo_id: UUID
    face_id: UUID
    frames: list[str] = Field(
        min_length=1, max_length=10, description="Base64 encoded selfie frames from guided scan"
    )
    front_only: bool = Field(default=False, description="Whether front-only fallback was used")
    note: str | None = Field(
        default=None, max_length=500, description="Optional note for organiser"
    )


class SubmitClaimResponse(BaseModel):
    """Result of submitting a 1:1 claim."""

    status: str = Field(description="'released' if face matched, or 'queued_for_review' if failed")
    photo_id: UUID


class FaceBBox(BaseModel):
    """Normalized or pixel bounding box coordinates for a face."""

    x: float
    y: float
    width: float
    height: float


class OrganiserClaimItem(BaseModel):
    """One pending claim item presented to the event organiser."""

    claim_id: UUID
    photo_id: UUID
    photo_url: str
    face_id: UUID | None
    face_bbox: FaceBBox | None
    claimant_email: str
    reason: str
    status: str
    created_at: datetime


class OrganiserClaimQueueResponse(BaseModel):
    """Pending claims list returned to the event organiser."""

    claims: list[OrganiserClaimItem]


class ReviewClaimRequest(BaseModel):
    """Review decision by the event organiser."""

    action: str = Field(
        pattern="^(approve|reject)$",
        description="Review action: approve or reject",
    )


class ReviewClaimResponse(BaseModel):
    """Result of organiser claim review."""

    claim_id: UUID
    status: str
