"""Pydantic API models for platform admin health monitoring."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EventHealthSummary(BaseModel):
    """Aggregate health and photo counts for one event. No personal or biometric data."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: UUID
    name: str
    country: str
    status: str
    created_at: datetime
    photos_quarantined: int
    photos_approved: int
    photos_indexed: int
    photos_failed: int
    photos_total: int
    indexing_progress_pct: float
    job_failures: int
    pending_claims: int
    not_me_count: int


class SystemHealthSummary(BaseModel):
    """System-level operational metrics (disk space, backup time)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    disk_free_bytes: int
    disk_total_bytes: int
    disk_used_percent: float
    last_backup_at: datetime | None


class AdminHealthResponse(BaseModel):
    """Admin dashboard operational status response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    events: list[EventHealthSummary]
    system: SystemHealthSummary
