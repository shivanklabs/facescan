"""Port: Private object storage with short-lived signed URLs."""

from typing import Protocol

from app.ports.capabilities import Capabilities


class ObjectStorage(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
