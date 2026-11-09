"""Authentication and authorisation helpers.

TEMPORARY DEV-ONLY ACCESS: until login (passkeys + email links) is built, the event, upload and
dev-match endpoints accept a single admin bearer token from FACESCAN_DEV_ADMIN_TOKEN, and only
when FACESCAN_ENV=dev. If the token is set in any other environment the API refuses to start
(check_dev_token_allowed). Remove this module's dev-token parts when login lands.

TODO: session tokens, role checks, guest-only face-search guard (invariant 5).
"""

import hmac

from fastapi import HTTPException, Request

from app.core.config import Settings

DEV_ADMIN_ACTOR = "dev-admin"


class DevTokenNotAllowedError(RuntimeError):
    """The dev admin token is configured outside the dev environment."""


def check_dev_token_allowed(settings: Settings) -> None:
    """
    Purpose: refuse to start when the temporary dev token is set outside FACESCAN_ENV=dev.
    Raises DevTokenNotAllowedError. Side effects: none.
    """
    if settings.dev_admin_token is not None and settings.env != "dev":
        raise DevTokenNotAllowedError(
            "FACESCAN_DEV_ADMIN_TOKEN is set but FACESCAN_ENV is not 'dev'; refusing to start"
        )


def dev_token_enabled(settings: Settings) -> bool:
    """Purpose: True when the temporary dev-only admin endpoints are switched on."""
    return settings.env == "dev" and settings.dev_admin_token is not None


def require_dev_admin(request: Request) -> str:
    """
    Purpose: FastAPI dependency for the TEMPORARY dev-only admin endpoints.
    Inputs: the request (Authorization: Bearer <token>).
    Outputs: the actor name for audit records. Raises 401 on a missing or wrong token.
    Side effects: none (the token is never logged).
    """
    settings: Settings = request.app.state.settings
    expected = settings.dev_admin_token
    if expected is None or settings.env != "dev":
        raise HTTPException(status_code=404, detail="not_found")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    ):
        raise HTTPException(
            status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}
        )
    return DEV_ADMIN_ACTOR
