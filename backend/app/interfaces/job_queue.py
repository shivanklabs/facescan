"""Port: Durable background job queue (Postgres table now)."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.interfaces.capabilities import Capabilities


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A job taken by one worker. payload holds ids only."""

    job_id: UUID
    event_id: UUID
    type: str
    payload: dict[str, object]
    attempts: int
    max_attempts: int


class JobQueue(Protocol):
    """Enqueue in the caller's event transaction; claim, finish and fail on its own."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...

    def enqueue(
        self, session: Session, event_id: UUID, job_type: str, payload: dict[str, object]
    ) -> UUID:
        """Purpose: add a job inside the caller's (event-scoped) transaction. Outputs: job id."""
        ...

    def claim(self, worker_id: str) -> ClaimedJob | None:
        """Purpose: take the next runnable job so no other worker gets it. None if idle."""
        ...

    def complete(self, job: ClaimedJob) -> None:
        """Purpose: mark a job done."""
        ...

    def fail(self, job: ClaimedJob, error: str) -> bool:
        """
        Purpose: record a failure; retry later with backoff, or mark failed after the last
            attempt. Outputs: True if the job will be retried. `error` must hold no personal
            data (an exception type name is enough).
        """
        ...
