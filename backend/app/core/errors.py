"""Centralised error handling: maps exceptions to HTTP responses without stack traces.

Domain errors carry a fixed error code and a short message written by the code itself (never
user data, file contents or vectors).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("facescan.errors")


class DomainError(Exception):
    """Base for errors that map to a client-facing HTTP status."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        """Purpose: keep a short, data-free message. Side effects: none."""
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    """The resource does not exist in this event (or the caller may not see it)."""

    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """The resource is in a state that does not allow the action."""

    status_code = 409
    code = "conflict"


class UnprocessableError(DomainError):
    """The request is well-formed but its content is not acceptable."""

    status_code = 422
    code = "unprocessable"


class PayloadTooLargeError(DomainError):
    """The request or a file is larger than allowed."""

    status_code = 413
    code = "payload_too_large"


class TooManyRequestsError(DomainError):
    """The caller exceeded the rate limit."""

    status_code = 429
    code = "rate_limited"


async def _domain(_request: Request, exc: Exception) -> JSONResponse:
    """Purpose: domain error -> its status and code. Side effects: none."""
    if not isinstance(exc, DomainError):
        return await _unhandled(_request, exc)
    return JSONResponse(
        status_code=exc.status_code, content={"error": exc.code, "detail": exc.message}
    )


async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    """Purpose: last-resort handler. Outputs: generic 500. Side effects: logs exception type."""
    logger.error("unhandled error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


def register_error_handlers(app: FastAPI) -> None:
    """Purpose: attach handlers. Inputs: app. Outputs: none. Side effects: mutates app."""
    app.add_exception_handler(DomainError, _domain)
    app.add_exception_handler(Exception, _unhandled)
