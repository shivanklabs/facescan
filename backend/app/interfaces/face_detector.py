"""Port: Finds faces and 5-point landmarks in an in-memory image."""

from typing import Protocol

from app.interfaces.capabilities import Capabilities


class FaceDetector(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
