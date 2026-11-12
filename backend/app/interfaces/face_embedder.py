"""Port: Turns an aligned face crop into an embedding vector."""

from typing import Protocol

from app.interfaces.capabilities import Capabilities


class FaceEmbedder(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
