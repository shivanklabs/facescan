"""HTTP routes for sign-in (magic links, passkeys), the account and sign-out."""

import base64
import binascii
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from app.core.login_sessions import CurrentUser, clear_session_cookie, set_session_cookie
from app.core.rate_limit import RateLimiter
from app.core.request_checks import Ctx, User, optional_user
from app.features.login import service
from app.features.login.schemas import (
    AgeAnswer,
    DevSignIn,
    GoogleConfigResponse,
    GoogleSignIn,
    MagicLinkRequest,
    MagicLinkVerify,
    MeResponse,
    PasskeyCredential,
    SignedOutResponse,
    SignInResponse,
)

router = APIRouter(prefix="/auth", tags=["sign-in"])
sign_in_limiter = RateLimiter("sign-in", limit=20, window_seconds=600)
MaybeUser = Annotated[CurrentUser | None, Depends(optional_user)]


def _b64url(value: object) -> bytes:
    """Purpose: decode a base64url field of a WebAuthn credential. Raises 422."""
    if not isinstance(value, str) or len(value) > 4096:
        raise HTTPException(status_code=422, detail="invalid credential")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid credential") from exc


def _challenge_of(credential: dict[str, object]) -> bytes:
    """Purpose: the challenge echoed in clientDataJSON (verified again by py_webauthn)."""
    response = credential.get("response")
    if not isinstance(response, dict):
        raise HTTPException(status_code=422, detail="invalid credential")
    try:
        client_data = json.loads(_b64url(response.get("clientDataJSON")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid credential") from exc
    if not isinstance(client_data, dict):
        raise HTTPException(status_code=422, detail="invalid credential")
    return _b64url(client_data.get("challenge"))


def _signed_in(ctx: Ctx, result: service.SignInResult) -> JSONResponse:
    """Purpose: response with the new session cookie."""
    response = JSONResponse(
        SignInResponse(next=result.next_path, has_passkey=result.has_passkey).model_dump()
    )
    set_session_cookie(response, ctx.settings, result.token)
    return response


@router.post("/magic-link", status_code=202, dependencies=[Depends(sign_in_limiter)])
def request_link(body: MagicLinkRequest, ctx: Ctx) -> dict[str, str]:
    """Purpose: email a sign-in link. Same answer whether or not the account exists."""
    service.request_magic_link(ctx, body.email, body.next)
    return {"status": "sent_if_allowed"}


@router.post("/magic-link/verify", dependencies=[Depends(sign_in_limiter)])
def verify_link(body: MagicLinkVerify, ctx: Ctx, user: MaybeUser) -> JSONResponse:
    """Purpose: consume a magic link and sign in (new session)."""
    try:
        result = service.verify_magic_link(ctx, body.token, user.session_id if user else None)
    except service.SignInError as exc:
        raise HTTPException(status_code=400, detail="link_invalid_or_expired") from exc
    return _signed_in(ctx, result)


@router.get("/google/config", response_model=GoogleConfigResponse)
def google_config(ctx: Ctx) -> GoogleConfigResponse:
    """
    Purpose: the public Google OAuth client id the sign-in button needs ("" when disabled)
        and whether the dev-only email sign-in is available.
    Inputs: ctx. Outputs: GoogleConfigResponse. Side effects: none.
    """
    return GoogleConfigResponse(
        client_id=ctx.settings.google_client_id.strip(),
        dev_login=service.dev_login_allowed(ctx),
    )


@router.post("/google", dependencies=[Depends(sign_in_limiter)])
def google_sign_in(body: GoogleSignIn, ctx: Ctx, user: MaybeUser) -> JSONResponse:
    """
    Purpose: sign in with a Google ID token. Inputs: body.credential, current session.
    Outputs: SignInResponse with the new session cookie; 401 when the token is rejected.
    Side effects: see service.sign_in_with_google.
    """
    try:
        result = service.sign_in_with_google(
            ctx, body.credential, user.session_id if user else None
        )
    except service.SignInError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _signed_in(ctx, result)


@router.post("/dev-login", dependencies=[Depends(sign_in_limiter)])
def dev_login(body: DevSignIn, ctx: Ctx, user: MaybeUser) -> JSONResponse:
    """
    Purpose: email-only sign-in for local development (FACESCAN_ENV=dev only; 403 otherwise).
    Inputs: body.email, current session. Outputs: SignInResponse with the session cookie.
    Side effects: see service.sign_in_dev.
    """
    result = service.sign_in_dev(ctx, body.email, user.session_id if user else None)
    return _signed_in(ctx, result)


@router.post("/passkey/register/options")
def passkey_register_options(user: User, ctx: Ctx) -> Response:
    """Purpose: WebAuthn creation options for the signed-in user."""
    return Response(service.registration_options(ctx, user), media_type="application/json")


@router.post("/passkey/register/verify")
def passkey_register(body: PasskeyCredential, user: User, ctx: Ctx) -> JSONResponse:
    """Purpose: store a new passkey; the session becomes a passkey session."""
    try:
        token = service.register_passkey(ctx, user, body.credential, _challenge_of(body.credential))
    except service.SignInError as exc:
        raise HTTPException(status_code=400, detail="passkey_not_registered") from exc
    response = JSONResponse({"has_passkey": True})
    set_session_cookie(response, ctx.settings, token)
    return response


@router.post("/passkey/login/options", dependencies=[Depends(sign_in_limiter)])
def passkey_login_options(ctx: Ctx) -> Response:
    """Purpose: WebAuthn request options (discoverable passkeys)."""
    return Response(service.authentication_options(ctx), media_type="application/json")


@router.post("/passkey/login/verify", dependencies=[Depends(sign_in_limiter)])
def passkey_login(body: PasskeyCredential, ctx: Ctx, user: MaybeUser) -> JSONResponse:
    """Purpose: sign in with a passkey (new passkey session)."""
    try:
        result = service.sign_in_with_passkey(
            ctx,
            body.credential,
            _challenge_of(body.credential),
            _b64url(body.credential.get("rawId")),
            user.session_id if user else None,
        )
    except service.SignInError as exc:
        raise HTTPException(status_code=400, detail="passkey_sign_in_failed") from exc
    return _signed_in(ctx, result)


@router.get("/me", response_model=MeResponse)
def get_me(user: User, ctx: Ctx) -> MeResponse:
    """Purpose: the signed-in user's flags (no email)."""
    m = service.me(ctx, user)
    return MeResponse(
        user_id=m.user_id,
        auth_method=m.auth_method,
        has_passkey=m.has_passkey,
        age_answered=m.age_answered,
        is_minor=m.is_minor,
        can_create_events=m.can_create_events,
        is_admin=m.is_admin,
        is_staff=m.is_staff,
    )


@router.post("/age", status_code=204)
def answer_age(body: AgeAnswer, user: User, ctx: Ctx) -> Response:
    """Purpose: record the age question (under-18s cannot use self-service search yet)."""
    service.answer_age(ctx, user, body.is_adult)
    return Response(status_code=204)


@router.post("/logout", response_model=SignedOutResponse)
def logout(user: User, ctx: Ctx) -> JSONResponse:
    """Purpose: end this session."""
    count = service.sign_out(ctx, user, everywhere=False)
    response = JSONResponse(SignedOutResponse(sessions_ended=count).model_dump())
    clear_session_cookie(response, ctx.settings)
    return response


@router.post("/logout-everywhere", response_model=SignedOutResponse)
def logout_everywhere(user: User, ctx: Ctx) -> JSONResponse:
    """Purpose: end every session of this user (all devices)."""
    count = service.sign_out(ctx, user, everywhere=True)
    response = JSONResponse(SignedOutResponse(sessions_ended=count).model_dump())
    clear_session_cookie(response, ctx.settings)
    return response
