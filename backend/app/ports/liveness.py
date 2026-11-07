"""Port: Decides whether selfie frames come from a live person."""

from typing import Protocol

from app.ports.capabilities import Capabilities


class Liveness(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
