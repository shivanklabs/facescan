"""Port: Per-event data keys wrapped by a master key (env now, KMS later)."""

from typing import Protocol

from app.ports.capabilities import Capabilities


class KeyManager(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
