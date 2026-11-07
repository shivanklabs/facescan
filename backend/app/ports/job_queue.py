"""Port: Durable background job queue (Postgres table now)."""

from typing import Protocol

from app.ports.capabilities import Capabilities


class JobQueue(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
