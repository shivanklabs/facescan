"""API models for the notice, consent, guest status and privacy settings."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NoticeResponse(BaseModel):
    """Versioned notice text (draft pending legal review)."""

    version: str
    draft: bool
    texts: dict[str, dict[str, str]]


class GuestStatusResponse(BaseModel):
    """The guest's state in an event."""

    roles: list[str]
    age_answered: bool
    is_minor: bool
    consent_current: bool
    notice_version: str
    search_enabled: bool
    dev_without_liveness: bool
    event_searchable: bool


class ConsentRequest(BaseModel):
    """Body: consent to the current notice and to face search in this event."""

    model_config = ConfigDict(extra="forbid", strict=True)
    notice_version: str = Field(min_length=1, max_length=50)
    language: Literal["en", "hi"]
    face_search: Literal[True]


class WithdrawResponse(BaseModel):
    """What withdrawal removed."""

    consents: int
    matches: int
    templates: int
    sessions: int


class ConsentRecordResponse(BaseModel):
    """One consent record."""

    event_id: UUID
    event_name: str
    purpose: str
    notice_version: str
    granted_at: datetime
    withdrawn_at: datetime | None


class MyConsentsResponse(BaseModel):
    """The user's consents."""

    consents: list[ConsentRecordResponse]
