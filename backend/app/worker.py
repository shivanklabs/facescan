"""Worker process entrypoint: claims jobs from the Postgres jobs table and runs their handlers.

Jobs are claimed with SELECT ... FOR UPDATE SKIP LOCKED (two workers never take the same job).
A failing job is retried with exponential backoff; after the last attempt it is marked failed
and the job type's failure handler runs (index_event marks the event failed). Logs carry job
ids, types and exception type names only, never payload contents or personal data.
Without a database configuration the worker runs an idle loop (local tooling, tests).
"""

import logging
import os
import signal
import socket
import threading
import time
from types import FrameType

from app.core.config import Settings, get_settings
from app.core.context import AppContext, ContextConfigError, build_context
from app.core.logging import configure_logging
from app.jobs.handlers import ON_FINAL_FAILURE, dispatch
from app.modules.scene_search.service import cleanup_expired_attempts
from app.ports.capabilities import Capabilities

logger = logging.getLogger("facescan.worker")


class NoEmailSender:
    """The worker never sends email; this keeps it independent of the email adapter."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description."""
        return Capabilities(adapter_name="none")

    def send_sign_in_link(self, to_address: str, link: str, expires_minutes: int) -> None:
        """Purpose: refuse (the worker has no reason to send sign-in links)."""
        raise RuntimeError("the worker does not send email")


def worker_id() -> str:
    """Purpose: identifier stored in jobs.locked_by (host and process id)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def run_one(ctx: AppContext, worker: str) -> bool:
    """
    Purpose: claim and run at most one job.
    Outputs: True if a job was processed (successfully or not), False if the queue was idle.
    Side effects: runs the job handler; updates the job row.
    """
    job = ctx.queue.claim(worker)
    if job is None:
        return False
    logger.info("job started", extra={"job_id": str(job.job_id), "job_type": job.type})
    try:
        dispatch(ctx, job.type, job.event_id, job.payload)
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded, then retried
        retry = ctx.queue.fail(job, type(exc).__name__)
        logger.warning(
            "job failed",
            extra={"job_id": str(job.job_id), "error": type(exc).__name__, "retry": retry},
            exc_info=True,
        )
        if not retry:
            handler = ON_FINAL_FAILURE.get(job.type)
            if handler is not None:
                handler(ctx, job.event_id)
        return True
    ctx.queue.complete(job)
    logger.info("job done", extra={"job_id": str(job.job_id)})
    return True


CLEANUP_INTERVAL_SECONDS = 3600.0


def run(
    settings: Settings,
    stop: threading.Event,
    max_iterations: int | None = None,
    ctx: AppContext | None = None,
) -> int:
    """
    Purpose: main poll loop with hourly expired attempt cleanup.
    Inputs: settings (poll interval), a stop event, optional iteration cap (tests), optional
        application context (None = idle loop without a database).
    Outputs: number of poll iterations performed.
    Side effects: runs jobs; cleans expired attempts at most hourly; sleeps between empty polls.
    """
    iterations = 0
    worker = worker_id()
    last_cleanup: float = 0.0
    logger.info("worker started", extra={"poll_interval": settings.worker_poll_interval_seconds})
    while not stop.is_set():
        busy = run_one(ctx, worker) if ctx is not None else False
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        if not busy:
            if ctx is not None:
                now = time.monotonic()
                if now - last_cleanup >= CLEANUP_INTERVAL_SECONDS:
                    last_cleanup = now
                    try:
                        cleanup_expired_attempts(ctx)
                    except Exception:  # noqa: BLE001
                        logger.warning("cleanup_expired_attempts failed", exc_info=True)
            stop.wait(settings.worker_poll_interval_seconds)
    logger.info("worker stopped")
    return iterations


def main() -> None:
    """
    Purpose: process entrypoint for `python -m app.worker`.
    Inputs: environment settings. Outputs: none.
    Side effects: installs SIGINT/SIGTERM handlers for graceful shutdown.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()

    def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        ctx: AppContext | None = build_context(settings, email=NoEmailSender())
    except ContextConfigError as exc:
        logger.warning("database features disabled: %s", exc)
        ctx = None
    run(settings, stop, ctx=ctx)


if __name__ == "__main__":
    main()
