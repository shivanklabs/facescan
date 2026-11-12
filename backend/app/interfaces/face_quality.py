"""Port: Scores whether a face crop is good enough to match."""

from typing import Protocol

from app.interfaces.capabilities import Capabilities


class FaceQuality(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
