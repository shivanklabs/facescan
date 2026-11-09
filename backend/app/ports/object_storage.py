"""Port: Private object storage with short-lived signed URLs.

Objects are always scoped to one event (invariant 1) and live in private buckets. Clients never
see a storage path; they get a short-lived signed URL (invariant 6).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.ports.capabilities import Capabilities


class ObjectNotFoundError(Exception):
    """The object does not exist (or was deleted)."""


class InvalidObjectReferenceError(ValueError):
    """Bucket name, object key or content type is not allowed."""


@dataclass(frozen=True, slots=True)
class SignedUrl:
    """A short-lived download URL. `url` is a capability: never log it."""

    url: str
    expires_at: datetime


class ObjectStorage(Protocol):
    """Private, event-scoped object storage."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...

    def put_object(self, bucket: str, event_id: UUID, data: bytes, content_type: str) -> str:
        """
        Purpose: store bytes under a new random object key.
        Inputs: bucket name, event id, data, content type (image/jpeg, image/png, image/webp).
        Outputs: the generated object key.
        Side effects: writes one object.
        """
        ...

    def get_object(self, bucket: str, event_id: UUID, object_key: str) -> bytes:
        """
        Purpose: read an object. Inputs: bucket, event id, object key. Outputs: bytes.
        Side effects: none. Raises ObjectNotFoundError if missing.
        """
        ...

    def delete_object(self, bucket: str, event_id: UUID, object_key: str) -> None:
        """Purpose: delete one object (missing is not an error). Side effects: deletes data."""
        ...

    def delete_event_objects(self, bucket: str, event_id: UUID) -> int:
        """
        Purpose: delete every object of an event in one bucket (data deletion runbook).
        Outputs: number of objects deleted. Side effects: deletes data.
        """
        ...

    def create_signed_download_url(self, bucket: str, event_id: UUID, object_key: str) -> SignedUrl:
        """
        Purpose: issue a short-lived download URL.
        Callers MUST run the permission check first (invariant 6); this method does not
        know who is asking.
        Outputs: SignedUrl. Side effects: none. Raises ObjectNotFoundError if missing.
        """
        ...
