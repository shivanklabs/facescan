"""Job type -> handler registry used by the worker."""

from collections.abc import Callable
from uuid import UUID

from app.core.context import AppContext
from app.features.photo_processing.service import index_event, mark_index_failed
from app.features.photo_upload.service import INDEX_EVENT_JOB

JobHandler = Callable[[AppContext, UUID, dict[str, object]], None]
FailureHandler = Callable[[AppContext, UUID], None]


def _index_event(ctx: AppContext, event_id: UUID, _payload: dict[str, object]) -> None:
    """Purpose: run the index_event job."""
    index_event(ctx, event_id)


HANDLERS: dict[str, JobHandler] = {INDEX_EVENT_JOB: _index_event}
ON_FINAL_FAILURE: dict[str, FailureHandler] = {INDEX_EVENT_JOB: mark_index_failed}


class UnknownJobTypeError(Exception):
    """A job type with no handler."""


def dispatch(ctx: AppContext, job_type: str, event_id: UUID, payload: dict[str, object]) -> None:
    """Purpose: run the handler for a job type. Raises UnknownJobTypeError."""
    handler = HANDLERS.get(job_type)
    if handler is None:
        raise UnknownJobTypeError(job_type)
    handler(ctx, event_id, payload)
