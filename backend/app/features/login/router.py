"""HTTP routes for sign-in (passkeys, staff keys, dev sign-in), the account and sign-out."""

import base64
import binascii
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from app.core.login_sessions import clear_session_cookie, set_session_cookie
from app.core.rate_limit import RateLimiter
from app.core.request_checks import Ctx, MaybeUser, PasskeyUser, SignedIn, User
from app.features.login import service
from app.features.login.schemas import (
    AgeAnswer,
    AuthConfigResponse,
    DeleteAccountRequest,
    DevSignIn,
    MeResponse,
    PasskeyCredential,
    RegisterOptionsRequest,
    RegisterRequest,
    SignedOutResponse,
    SignInResponse,
    StaffKeyCreate,
    StaffKeyCreated,
    StaffKeyItem,
    StaffKeyList,
    StaffKeyRedeem,
)

router = APIRouter(prefix="/auth", tags=["sign-in"])
sign_in_limiter = RateLimiter("sign-in", limit=20, window_seconds=600)


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


@router.get("/config", response_model=AuthConfigResponse)
def auth_config(ctx: Ctx) -> AuthConfigResponse:
    """
    Purpose: what the sign-in page may show (the dev-only email sign-in exists only when
        FACESCAN_ENV=dev). Inputs: ctx. Outputs: AuthConfigResponse. Side effects: none.
    """
    return AuthConfigResponse(dev_login=service.dev_login_allowed(ctx))


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


@router.post("/register/options", dependencies=[Depends(sign_in_limiter)])
def register_options(body: RegisterOptionsRequest, ctx: Ctx) -> Response:
    """
    Purpose: start the common registration (guest, organiser or photographer; never admin).
    Inputs: name, email, account type. Outputs: WebAuthn creation options.
    Side effects: stores a single-use challenge. 409 email_taken, 422 bad input.
    """
    try:
        options = service.new_account_options(ctx, body.name.strip(), body.email)
    except service.EmailTakenError as exc:
        raise HTTPException(status_code=409, detail="email_taken") from exc
    return Response(options, media_type="application/json")


@router.post("/register", dependencies=[Depends(sign_in_limiter)])
def register(body: RegisterRequest, ctx: Ctx, user: MaybeUser) -> JSONResponse:
    """
    Purpose: finish the registration: verify the passkey, create the account (guests active,
        organisers / photographers pending approval) and sign in.
    Outputs: {next: the person's dashboard, has_passkey}. 400 bad passkey, 409 email_taken.
    """
    try:
        result = service.sign_up_with_passkey(
            ctx,
            body.credential,
            _challenge_of(body.credential),
            body.name.strip(),
            body.email,
            body.account_type,
            user.session_id if user else None,
        )
    except service.EmailTakenError as exc:
        raise HTTPException(status_code=409, detail="email_taken") from exc
    except service.SignInError as exc:
        raise HTTPException(status_code=400, detail="passkey_not_registered") from exc
    return _signed_in(ctx, result)


@router.post("/staff-key", dependencies=[Depends(sign_in_limiter)])
def use_staff_key(body: StaffKeyRedeem, ctx: Ctx, user: MaybeUser) -> JSONResponse:
    """
    Purpose: use a one-time staff key. Signed in: the role is added to this account.
        Signed out: a new account (no email) with a session; add a passkey next.
    Outputs: SignInResponse (cookie set only for a new session).
    """
    try:
        result = service.redeem_staff_key(ctx, body.key, user)
    except service.SignInError as exc:
        raise HTTPException(status_code=400, detail="staff_key_invalid") from exc
    if result is None:
        return JSONResponse(SignInResponse(next="/studio", has_passkey=False).model_dump())
    return _signed_in(ctx, result)


def _key_item(view: service.StaffKeyView) -> StaffKeyItem:
    """Purpose: service view -> API model."""
    return StaffKeyItem(
        key_id=view.key_id,
        role=view.role,
        event_id=view.event_id,
        created_at=view.created_at,
        expires_at=view.expires_at,
        used=view.used,
        revoked=view.revoked,
    )


@router.post("/staff-keys", status_code=201, response_model=StaffKeyCreated)
def create_staff_key(body: StaffKeyCreate, user: PasskeyUser, ctx: Ctx) -> StaffKeyCreated:
    """Purpose: create a one-time staff key (admins: admin/organiser; organisers: photographer)."""
    key, view = service.create_staff_key(ctx, user, body.role, body.event_id)
    return StaffKeyCreated(key=key, **_key_item(view).model_dump())


@router.get("/staff-keys", response_model=StaffKeyList)
def list_staff_keys(user: PasskeyUser, ctx: Ctx) -> StaffKeyList:
    """Purpose: the staff keys the caller created (never the keys themselves)."""
    return StaffKeyList(keys=[_key_item(v) for v in service.list_staff_keys(ctx, user)])


@router.post("/staff-keys/{key_id}/revoke", status_code=204)
def revoke_staff_key(key_id: UUID, user: PasskeyUser, ctx: Ctx) -> Response:
    """Purpose: turn off an unused staff key the caller created."""
    service.revoke_staff_key(ctx, user, key_id)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
def get_me(user: SignedIn, ctx: Ctx) -> MeResponse:
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
        role=m.role,
        status=m.status,
        display_name=m.display_name,
        home=m.home,
    )


@router.post("/age", status_code=204)
def answer_age(body: AgeAnswer, user: User, ctx: Ctx) -> Response:
    """Purpose: record the age question (under-18s cannot use self-service search yet)."""
    service.answer_age(ctx, user, body.is_adult)
    return Response(status_code=204)


@router.post("/logout", response_model=SignedOutResponse)
def logout(user: SignedIn, ctx: Ctx) -> JSONResponse:
    """Purpose: end this session."""
    count = service.sign_out(ctx, user, everywhere=False)
    response = JSONResponse(SignedOutResponse(sessions_ended=count).model_dump())
    clear_session_cookie(response, ctx.settings)
    return response


@router.post("/logout-everywhere", response_model=SignedOutResponse)
def logout_everywhere(user: SignedIn, ctx: Ctx) -> JSONResponse:
    """Purpose: end every session of this user (all devices)."""
    count = service.sign_out(ctx, user, everywhere=True)
    response = JSONResponse(SignedOutResponse(sessions_ended=count).model_dump())
    clear_session_cookie(response, ctx.settings)
    return response


@router.post("/delete-account", status_code=200, dependencies=[Depends(sign_in_limiter)])
def delete_account(body: DeleteAccountRequest, user: SignedIn, ctx: Ctx) -> JSONResponse:
    """
    Purpose: erase my own account and my data (typed confirmation "DELETE"). Event photos
        stay. Outputs: {"deleted": true}; the session cookie is cleared.
    Side effects: see service.delete_my_account.
    """
    service.delete_my_account(ctx, user)
    response = JSONResponse({"deleted": True})
    clear_session_cookie(response, ctx.settings)
    return response
