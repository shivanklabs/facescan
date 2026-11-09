"""Port: Per-event data keys wrapped by a master key (env now, KMS later)."""

from typing import Protocol
from uuid import UUID

from app.ports.capabilities import Capabilities


class KeyUnwrapError(Exception):
    """The wrapped key cannot be opened (wrong master key, wrong event or tampered)."""


class KeyManager(Protocol):
    """Wraps and unwraps 32-byte event data keys. The master key never leaves the adapter."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...

    @property
    def master_key_id(self) -> str:
        """Identifier of the master key in use (stored next to each wrapped key)."""
        ...

    def wrap(self, event_id: UUID, data_key: bytes) -> bytes:
        """
        Purpose: encrypt an event's data key under the master key, bound to the event id.
        Outputs: wrapped bytes. Side effects: none.
        """
        ...

    def derive_key(self, purpose: str) -> bytes:
        """
        Purpose: a stable 32-byte key for one non-event purpose (e.g. email hashing), derived
        from the master key so no extra secret is needed. Side effects: none.
        """
        ...

    def unwrap(self, event_id: UUID, wrapped: bytes) -> bytes:
        """
        Purpose: recover an event's data key. Outputs: 32 bytes. Side effects: none.
        Raises KeyUnwrapError if the bytes were not wrapped for this event by this master key.
        """
        ...
