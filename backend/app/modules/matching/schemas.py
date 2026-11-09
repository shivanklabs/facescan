"""API models for the TEMPORARY dev match endpoint: photo ids per band, nothing else."""

from uuid import UUID

from pydantic import BaseModel


class MatchBandsResponse(BaseModel):
    """Photo ids per band. Never vectors, never scores (invariant 4)."""

    event_id: UUID
    release: list[UUID]
    borderline: list[UUID]
