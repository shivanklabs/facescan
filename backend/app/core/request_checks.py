"""FastAPI dependencies: application context, signed-in user, roles, CSRF guard."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.login_sessions import SESSION_COOKIE, CurrentUser, admin_allowed, lookup_session
from app.core.services_setup import AppContext
from app.db.event_isolation import user_transaction
from app.features.events.models import EventMember
from app.features.login.models import User as UserModel

STAFF_ROLES = ("organiser", "photographer")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def get_ctx(request: Request) -> AppContext:
    """Purpose: the application context (built at startup)."""
    ctx: AppContext | None = request.app.state.ctx
    if ctx is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


def optional_user(request: Request, ctx: Ctx) -> CurrentUser | None:
    """
    Purpose: the signed-in user, or None. Side effects: updates the session's last_seen_at.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 100:
        return None
    with ctx.sessions() as session, session.begin():
        row = lookup_session(session, token)
        if row is None:
            return None
        row.last_seen_at = datetime.now(UTC)
        account = session.get(UserModel, row.user_id)
        status = account.status if account is not None else "suspended"
        return CurrentUser(row.user_id, row.id, row.auth_method, row.event_id, status)


def signed_in(user: Annotated[CurrentUser | None, Depends(optional_user)]) -> CurrentUser:
    """
    Purpose: require a session whatever the account status, for the few routes a suspended
        person still needs (who am I, sign out, delete my account). 401 when signed out.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="sign_in_required")
    return user


def current_user(user: Annotated[CurrentUser, Depends(signed_in)]) -> CurrentUser:
    """
    Purpose: require a signed-in account that is not suspended (401 / 403 otherwise). Every
        other route uses this, so a suspended account is locked out everywhere at once, even
        when someone types a dashboard or API address by hand.
    """
    if user.account_status == "suspended":
        raise HTTPException(status_code=403, detail="account_suspended")
    return user


SignedIn = Annotated[CurrentUser, Depends(signed_in)]
User = Annotated[CurrentUser, Depends(current_user)]
MaybeUser = Annotated[CurrentUser | None, Depends(optional_user)]


def staff_refusal(account: UserModel | None) -> str:
    """
    Purpose: the reason code shown when a staff feature is refused, so the site can say
        "waiting for approval" / "not approved" instead of a bare error.
    Inputs: the account row. Outputs: account_pending / account_rejected / not_allowed.
    """
    if account is not None and account.role in ("organiser", "photographer"):
        if account.status == "pending":
            return "account_pending"
        if account.status == "rejected":
            return "account_rejected"
    return "not_allowed"


def passkey_user(user: User) -> CurrentUser:
    """
    Purpose: require a strong sign-in for staff pages: a passkey, or the dev-only sign-in
        (exists only when FACESCAN_ENV=dev). A staff-key session without a passkey is
        refused until the person adds one.
    Inputs: the signed-in user. Outputs: the same user. Side effects: none.
    """
    if user.auth_method not in ("passkey", "dev"):
        raise HTTPException(status_code=403, detail="passkey_required")
    return user


PasskeyUser = Annotated[CurrentUser, Depends(passkey_user)]


def admin_user(user: PasskeyUser, ctx: Ctx) -> CurrentUser:
    """
    Purpose: require a platform admin (set by a redeemed admin staff key).
    Inputs: user (PasskeyUser), ctx (AppContext).
    Outputs: CurrentUser.
    Side effects: raises HTTPException(403) if not authorized.
    """
    with user_transaction(ctx.sessions, user.user_id) as session:
        account = session.get(UserModel, user.user_id)
        if account is None or not admin_allowed(account):
            raise HTTPException(status_code=403, detail="admin_required")
    return user


AdminUser = Annotated[CurrentUser, Depends(admin_user)]


def roles_in_event(session: Session, event_id: UUID, user_id: UUID) -> set[str]:
    """Purpose: the user's roles in one event (call inside that event's transaction)."""
    return set(
        session.scalars(
            select(EventMember.role).where(
                EventMember.event_id == event_id, EventMember.user_id == user_id
            )
        )
    )


def require_role(
    session: Session, event_id: UUID, user_id: UUID, allowed: tuple[str, ...]
) -> set[str]:
    """
    Purpose: check the user holds one of the allowed roles in this event (checked on every
        request). Outputs: the user's roles. Raises 404 when the user is not a member at all
        (the event's existence is not revealed) and 403 for the wrong role.
    """
    roles = roles_in_event(session, event_id, user_id)
    if not roles:
        raise HTTPException(status_code=404, detail="not_found")
    if not roles & set(allowed):
        raise HTTPException(status_code=403, detail="forbidden")
    return roles


_guard_logger = logging.getLogger("facescan.origin")


def same_origin_guard(
    allowed_origin: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """
    Purpose: middleware refusing state-changing requests from another origin (CSRF), on top
        of SameSite=Strict cookies. Requests without an Origin header (non-browser clients)
        pass; browsers always send Origin on cross-origin requests.
    """

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin.rstrip("/") != allowed_origin.rstrip("/"):
                # The origin is a site address (no personal data); logging it shows which
                # address to put in FACESCAN_AUTH_RP_ORIGIN.
                _guard_logger.warning(
                    "refused cross-origin %s %s from origin %s (allowed: %s)",
                    request.method,
                    request.url.path,
                    origin[:200],
                    allowed_origin,
                )
                return JSONResponse({"error": "cross_origin"}, status_code=403)
        return await call_next(request)

    return middleware


async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """
    Purpose: middleware adding browser security headers to every response, and caching rules:
        hashed /assets/ files are cached for a year (their name changes with their content);
        everything else (API answers, the page itself) is never cached unless the route set
        its own Cache-Control.
    Inputs: request, next handler. Outputs: the response with headers added.
    Side effects: none beyond headers; never overwrites a header a route already set.
    """
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    # Camera only for this site (the face scan); no microphone, location or payment APIs.
    headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
    if request.url.path.startswith("/assets/"):
        headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    else:
        headers.setdefault("Cache-Control", "no-store")
    return response
