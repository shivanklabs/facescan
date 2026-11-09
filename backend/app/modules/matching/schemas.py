"""API models for guest search: released photo ids only (never vectors, scores or borderline)."""

from uuid import UUID

from pydantic import BaseModel


class SearchResponse(BaseModel):
    """The release band and a hint that some photos may be missing."""

    release: list[UUID]
    some_photos_missing: bool
