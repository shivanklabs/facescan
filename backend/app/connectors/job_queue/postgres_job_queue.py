"""JobQueue adapter on the Postgres jobs table using SELECT ... FOR UPDATE SKIP LOCKED.

Claiming runs in the 'dispatch' RLS scope (jobs rows only; no biometric tables). Because that
scope can read every job's payload, payloads may hold only ids: enqueue() refuses anything but
short lowercase codes, UUIDs, integers and booleans (validate_payload). A job that
stays 'running' longer than the lock timeout (crashed worker) becomes claimable again. Failed
attempts are retried with exponential backoff until max_attempts, then marked 'failed'.
"""

import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.background_jobs.job_table import Job
from app.db.event_isolation import DISPATCH_SCOPE, scoped_transaction
from app.interfaces.capabilities import Capabilities
from app.interfaces.job_queue import ClaimedJob

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_PAYLOAD_KEYS = 10


class InvalidJobPayloadError(ValueError):
    """A job payload holds something other than ids (it might carry personal data)."""


def validate_payload(payload: dict[str, object]) -> dict[str, object]:
    """
    Purpose: allow only id-like payloads (keys: snake_case; values: UUID strings, short
        lowercase codes, ints, bools). Raises InvalidJobPayloadError naming the key only.
    """
    if len(payload) > MAX_PAYLOAD_KEYS:
        raise InvalidJobPayloadError("too many payload keys")
    for key, value in payload.items():
        if not _KEY_RE.fullmatch(key):
            raise InvalidJobPayloadError("payload key is not snake_case")
        if isinstance(value, bool | int):
            continue
        if isinstance(value, str) and (_UUID_RE.fullmatch(value) or _CODE_RE.fullmatch(value)):
            continue
        raise InvalidJobPayloadError(f"payload value for '{key}' is not an id")
    return payload


_CLAIM_SQL = text(
    """
    WITH next AS (
        SELECT id FROM jobs
        WHERE (status = 'queued' AND run_after <= now())
           OR (status = 'running' AND locked_at < now() - make_interval(secs => :lock_timeout))
        ORDER BY run_after, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE jobs SET status = 'running', attempts = jobs.attempts + 1,
                    locked_by = :worker, locked_at = now(), updated_at = now()
    FROM next WHERE jobs.id = next.id
    RETURNING jobs.id, jobs.event_id, jobs.type, jobs.payload, jobs.attempts, jobs.max_attempts
    """
)
_COMPLETE_SQL = text(
    "UPDATE jobs SET status = 'done', locked_by = NULL, locked_at = NULL, last_error = NULL, "
    "updated_at = now() WHERE id = :id AND status = 'running'"
)
_FAIL_SQL = text(
    """
    UPDATE jobs SET
        status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
        run_after = now() + make_interval(secs => :base * power(2, attempts - 1)),
        locked_by = NULL, locked_at = NULL, last_error = :error, updated_at = now()
    WHERE id = :id AND status = 'running'
    RETURNING status
    """
)


class PostgresJobQueue:
    """Jobs table queue."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        max_attempts: int,
        retry_base_seconds: float,
        lock_timeout_seconds: float,
    ) -> None:
        """Purpose: configure retries and the stale-lock timeout. Side effects: none."""
        self._factory = factory
        self._max_attempts = max_attempts
        self._retry_base = retry_base_seconds
        self._lock_timeout = lock_timeout_seconds

    @property
    def capabilities(self) -> Capabilities:
        """Static description: free, local Postgres."""
        return Capabilities(adapter_name="postgres")

    def enqueue(
        self, session: Session, event_id: UUID, job_type: str, payload: dict[str, object]
    ) -> UUID:
        """
        Purpose: insert a queued job in the caller's event transaction. Outputs: job id.
        Raises InvalidJobPayloadError unless the payload holds only ids.
        """
        if not _CODE_RE.fullmatch(job_type):
            raise InvalidJobPayloadError("job type must be a short lowercase code")
        job = Job(
            event_id=event_id,
            type=job_type,
            payload=validate_payload(payload),
            max_attempts=self._max_attempts,
        )
        session.add(job)
        session.flush()
        return job.id

    def claim(self, worker_id: str) -> ClaimedJob | None:
        """Purpose: atomically take one runnable job (skipping rows other workers locked)."""
        with scoped_transaction(self._factory, DISPATCH_SCOPE) as session:
            row = session.execute(
                _CLAIM_SQL, {"worker": worker_id[:100], "lock_timeout": self._lock_timeout}
            ).one_or_none()
        if row is None:
            return None
        return ClaimedJob(
            job_id=row.id,
            event_id=row.event_id,
            type=row.type,
            payload=dict(row.payload),
            attempts=row.attempts,
            max_attempts=row.max_attempts,
        )

    def complete(self, job: ClaimedJob) -> None:
        """Purpose: mark a running job done."""
        with scoped_transaction(self._factory, DISPATCH_SCOPE) as session:
            session.execute(_COMPLETE_SQL, {"id": job.job_id})

    def fail(self, job: ClaimedJob, error: str) -> bool:
        """Purpose: schedule a retry or mark failed. Outputs: True if it will be retried."""
        with scoped_transaction(self._factory, DISPATCH_SCOPE) as session:
            status = session.execute(
                _FAIL_SQL, {"id": job.job_id, "error": error[:200], "base": self._retry_base}
            ).scalar_one_or_none()
        return status == "queued"
