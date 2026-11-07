"""Centralised error handling: maps exceptions to HTTP responses without stack traces.

TODO: add domain error types (NotFound, Forbidden, Conflict) with exact status codes.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("facescan.errors")


async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    """Purpose: last-resort handler. Outputs: generic 500. Side effects: logs exception type."""
    logger.error("unhandled error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


def register_error_handlers(app: FastAPI) -> None:
    """Purpose: attach handlers. Inputs: app. Outputs: none. Side effects: mutates app."""
    app.add_exception_handler(Exception, _unhandled)
