"""Port: Nearest-neighbour search over one event's decrypted embeddings, in memory."""

from typing import Protocol

from app.interfaces.capabilities import Capabilities


class VectorSearch(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
