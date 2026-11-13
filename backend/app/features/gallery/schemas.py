"""API models for the guest gallery: claim notices (no scores, no other guests)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ClaimNotice(BaseModel):
    """Notice of a reviewed claim for the guest."""

    claim_id: UUID
    photo_id: UUID
    status: str
    reason: str
    reviewed_at: datetime | None
