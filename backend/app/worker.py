"""Worker process entrypoint: polls the Postgres jobs table and runs job handlers.

For now it runs an idle loop. TODO: claim jobs via the JobQueue port and dispatch through
app.jobs.handlers.
"""

import logging
import signal
import threading
from types import FrameType

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

logger = logging.getLogger("facescan.worker")


def run(settings: Settings, stop: threading.Event, max_iterations: int | None = None) -> int:
    """
    Purpose: main poll loop.
    Inputs: settings (poll interval), a stop event, optional iteration cap (tests).
    Outputs: number of poll iterations performed.
    Side effects: sleeps between polls; logs start/stop only (never job payloads).
    """
    iterations = 0
    logger.info("worker started", extra={"poll_interval": settings.worker_poll_interval_seconds})
    while not stop.is_set():
        # TODO: job = queue.claim_next(); handlers.dispatch(job)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
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
    run(settings, stop)


if __name__ == "__main__":
    main()
