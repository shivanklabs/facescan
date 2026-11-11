"""Sign-in: email magic links and passkeys (WebAuthn), plus sessions and the age answer.

- Magic links: random token, only its SHA-256 stored, single use (atomic UPDATE ... WHERE
  used_at IS NULL), expire after FACESCAN_AUTH_MAGIC_LINK_MINUTES (15). Requests are capped
  per address per hour. The response never reveals whether an account exists.
- Passkeys: py_webauthn; challenges stored server-side, single use, 5 minutes. Organisers and
  photographers must use a passkey; guests are offered one after their first sign-in.
- Every sign-in creates a new session (rotation) and revokes the one it replaces.
"""

import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.auth import (
    EMAIL_RE,
    CurrentUser,
    admin_allowed,
    create_session,
    email_hash,
    find_or_create_user,
    hash_token,
    new_token,
    normalise_email,
    open_email,
    organiser_allowed,
    revoke_sessions,
    seal_email,
)
from app.core.context import AppContext
from app.core.errors import ForbiddenError, UnprocessableError
from app.db.event_scope import ADMIN_SCOPE, scoped_transaction, user_transaction
from app.modules.audit.service import record
from app.modules.events.models import EventMember
from app.modules.identity.models import AuthChallenge, MagicLink, Passkey, User

CHALLENGE_MINUTES = 5
NEXT_PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9/_\-.?=&%]{0,199}$")
EVENT_PATH_RE = re.compile(r"^/e/([0-9a-f-]{36})(?:[/?].*)?$")


class SignInError(Exception):
    """A sign-in attempt failed (message is generic on purpose)."""


@dataclass(frozen=True, slots=True)
class SignInResult:
    """A new session and where to go next."""

    token: str
    user_id: UUID
    next_path: str
    has_passkey: bool


def safe_next_path(next_path: str | None) -> str:
    """Purpose: allow only same-site relative paths (no open redirects)."""
    if next_path and NEXT_PATH_RE.fullmatch(next_path):
        return next_path
    return "/"


def event_from_path(next_path: str) -> UUID | None:
    """Purpose: the event id of an invite path (/e/<event_id>...), if any."""
    match = EVENT_PATH_RE.match(next_path)
    try:
        return UUID(match.group(1)) if match else None
    except ValueError:
        return None


def audit_global(
    ctx: AppContext,
    actor: str,
    action: str,
    details: dict[str, int | str | bool | None] | None = None,
) -> None:
    """Purpose: audit row without an event (admin scope). Side effects: one insert."""
    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as session:
        record(session, event_id=None, actor=actor, action=action, details=details)


# ---------------------------------------------------------------- magic links


def is_staff_email(ctx: AppContext, email: str) -> bool:
    """
    Purpose: Check if an email address belongs to staff (organiser, admin, or event photographer).
    Inputs:
        ctx: AppContext with settings, keys and session factory
        email: normalised email address string
    Outputs:
        bool: True if address belongs to staff, False otherwise
    Side Effects:
        Queries database for user and event roles if not in configured admin/organiser lists.
    """
    settings = ctx.settings
    digest = email_hash(ctx.keys, email)
    allowed = {
        email_hash(ctx.keys, normalise_email(a))
        for a in f"{settings.auth_organiser_emails},{settings.auth_admin_emails}".split(",")
        if a.strip() and EMAIL_RE.fullmatch(a.strip().lower())
    }
    if any(hmac.compare_digest(digest, h) for h in allowed):
        return True

    with ctx.sessions() as session:
        user = session.scalars(select(User).where(User.email_hash == digest)).first()
        if user is None:
            return False
    with user_transaction(ctx.sessions, user.id) as session:
        has_staff_role = session.scalar(
            select(func.count())
            .select_from(EventMember)
            .where(
                EventMember.user_id == user.id,
                EventMember.role.in_(("organiser", "photographer")),
            )
        )
        return bool(has_staff_role and has_staff_role > 0)


def request_magic_link(ctx: AppContext, address: str, next_path: str | None) -> None:
    """
    Purpose: email a single-use sign-in link to staff only
    (silently does nothing over the hourly cap).
    Raises UnprocessableError for a malformed address; ForbiddenError for non-staff emails.
    Side effects: inserts magic_links; sends one email.
    """
    settings = ctx.settings
    try:
        email = normalise_email(address)
    except ValueError as exc:
        raise UnprocessableError("enter a valid email address") from exc
    if not is_staff_email(ctx, email):
        raise ForbiddenError("magic links are for staff only; guests join without email")
    digest = email_hash(ctx.keys, email)
    token = new_token()
    now = datetime.now(UTC)
    with ctx.sessions() as session, session.begin():
        recent = session.scalar(
            select(func.count())
            .select_from(MagicLink)
            .where(MagicLink.email_hash == digest, MagicLink.created_at > now - timedelta(hours=1))
        )
        if int(recent or 0) >= settings.auth_magic_links_per_hour:
            return
        session.add(
            MagicLink(
                token_hash=hash_token(token),
                email_hash=digest,
                email_encrypted=seal_email(ctx.keys, email),
                next_path=safe_next_path(next_path),
                expires_at=now + timedelta(minutes=settings.auth_magic_link_minutes),
            )
        )
    link = f"{settings.auth_rp_origin.rstrip('/')}/auth/callback#token={token}"
    ctx.email.send_sign_in_link(email, link, settings.auth_magic_link_minutes)


def verify_magic_link(ctx: AppContext, token: str, current_session: UUID | None) -> SignInResult:
    """
    Purpose: consume a magic link and start a session. Raises SignInError (unknown, used or
        expired: same message). Side effects: marks the link used; creates the user on first
        sign-in; rotates the session; writes an audit row.
    """
    if not token or len(token) > 100:
        raise SignInError("link invalid or expired")
    now = datetime.now(UTC)
    with ctx.sessions() as session, session.begin():
        link = session.execute(
            update(MagicLink)
            .where(
                MagicLink.token_hash == hash_token(token),
                MagicLink.used_at.is_(None),
                MagicLink.expires_at > now,
            )
            .values(used_at=now)
            .returning(MagicLink.email_encrypted, MagicLink.next_path)
        ).first()
        if link is None:
            raise SignInError("link invalid or expired")
        user = find_or_create_user(session, ctx.keys, open_email(ctx.keys, link.email_encrypted))
        has_passkey = bool(
            session.scalar(
                select(func.count()).select_from(Passkey).where(Passkey.user_id == user.id)
            )
        )
        session_token = create_session(
            session,
            ctx.settings,
            user.id,
            "magic_link",
            event_from_path(link.next_path),
            replaces=current_session,
        )
        user_id, next_path = user.id, link.next_path
    audit_global(ctx, f"user:{user_id}", "auth.signed_in", {"method": "magic_link"})
    return SignInResult(session_token, user_id, next_path, has_passkey)


# ---------------------------------------------------------------- passkeys


def _store_challenge(ctx: AppContext, challenge: bytes, purpose: str, user_id: UUID | None) -> None:
    """Purpose: remember a challenge for one ceremony."""
    with ctx.sessions() as session, session.begin():
        session.add(
            AuthChallenge(
                challenge=challenge,
                purpose=purpose,
                user_id=user_id,
                expires_at=datetime.now(UTC) + timedelta(minutes=CHALLENGE_MINUTES),
            )
        )


def _consume_challenge(
    ctx: AppContext, challenge: bytes, purpose: str, user_id: UUID | None
) -> bool:
    """Purpose: use a stored challenge exactly once. Outputs: True if it was valid."""
    now = datetime.now(UTC)
    with ctx.sessions() as session, session.begin():
        query = update(AuthChallenge).where(
            AuthChallenge.challenge == challenge,
            AuthChallenge.purpose == purpose,
            AuthChallenge.used_at.is_(None),
            AuthChallenge.expires_at > now,
        )
        if user_id is not None:
            query = query.where(AuthChallenge.user_id == user_id)
        return (
            session.execute(query.values(used_at=now).returning(AuthChallenge.id)).first()
            is not None
        )


def registration_options(ctx: AppContext, user: CurrentUser) -> str:
    """Purpose: WebAuthn creation options (JSON) for the signed-in user."""
    challenge = secrets.token_bytes(32)
    with ctx.sessions() as session:
        existing = list(
            session.scalars(select(Passkey.credential_id).where(Passkey.user_id == user.user_id))
        )
    options = generate_registration_options(
        rp_id=ctx.settings.auth_rp_id,
        rp_name=ctx.settings.auth_rp_name,
        user_id=user.user_id.bytes,
        user_name=f"user-{str(user.user_id)[:8]}",  # never the email
        challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing],
    )
    _store_challenge(ctx, challenge, "register", user.user_id)
    return options_to_json(options)


def register_passkey(
    ctx: AppContext, user: CurrentUser, credential: dict[str, object], challenge: bytes
) -> str:
    """
    Purpose: verify and store a new passkey, then upgrade the session to a passkey session.
    Outputs: the new session token. Raises SignInError.
    """
    if not _consume_challenge(ctx, challenge, "register", user.user_id):
        raise SignInError("passkey registration expired; try again")
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=ctx.settings.auth_rp_id,
            expected_origin=ctx.settings.auth_rp_origin,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise SignInError("passkey could not be verified") from exc
    with ctx.sessions() as session, session.begin():
        session.add(
            Passkey(
                user_id=user.user_id,
                credential_id=verified.credential_id,
                public_key=verified.credential_public_key,
                sign_count=verified.sign_count,
            )
        )
        token = create_session(
            session,
            ctx.settings,
            user.user_id,
            "passkey",
            user.session_event_id,
            replaces=user.session_id,
        )
    audit_global(ctx, user.actor, "auth.passkey_registered")
    return token


def authentication_options(ctx: AppContext) -> str:
    """Purpose: WebAuthn request options for discoverable passkeys (no email needed)."""
    challenge = secrets.token_bytes(32)
    options = generate_authentication_options(
        rp_id=ctx.settings.auth_rp_id,
        challenge=challenge,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _store_challenge(ctx, challenge, "authenticate", None)
    return options_to_json(options)


def sign_in_with_passkey(
    ctx: AppContext,
    credential: dict[str, object],
    challenge: bytes,
    credential_id: bytes,
    current_session: UUID | None,
) -> SignInResult:
    """
    Purpose: verify a passkey assertion and start a passkey session. Raises SignInError.
    Side effects: updates the passkey's sign count; rotates the session; audit row.
    """
    if not _consume_challenge(ctx, challenge, "authenticate", None):
        raise SignInError("sign-in expired; try again")
    with ctx.sessions() as session, session.begin():
        passkey = session.scalars(
            select(Passkey).where(Passkey.credential_id == credential_id)
        ).first()
        if passkey is None:
            raise SignInError("passkey not recognised")
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=ctx.settings.auth_rp_id,
                expected_origin=ctx.settings.auth_rp_origin,
                credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except InvalidAuthenticationResponse as exc:
            raise SignInError("passkey could not be verified") from exc
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = datetime.now(UTC)
        token = create_session(
            session, ctx.settings, passkey.user_id, "passkey", None, replaces=current_session
        )
        user_id = passkey.user_id
    audit_global(ctx, f"user:{user_id}", "auth.signed_in", {"method": "passkey"})
    return SignInResult(token, user_id, "/", True)


# ---------------------------------------------------------------- account


@dataclass(frozen=True, slots=True)
class Me:
    """What the app needs to know about the signed-in user (no email in API responses)."""

    user_id: UUID
    auth_method: str
    has_passkey: bool
    age_answered: bool
    is_minor: bool
    can_create_events: bool
    is_admin: bool = False


def me(ctx: AppContext, user: CurrentUser) -> Me:
    """Purpose: the signed-in user's account flags."""
    with ctx.sessions() as session:
        row = session.get(User, user.user_id)
        if row is None:
            raise SignInError("account not found")
        has_passkey = bool(
            session.scalar(
                select(func.count()).select_from(Passkey).where(Passkey.user_id == user.user_id)
            )
        )
        return Me(
            user_id=user.user_id,
            auth_method=user.auth_method,
            has_passkey=has_passkey,
            age_answered=row.age_answered_at is not None,
            is_minor=row.is_minor,
            can_create_events=organiser_allowed(ctx.settings, ctx.keys, row),
            is_admin=admin_allowed(ctx.settings, ctx.keys, row),
        )


def answer_age(ctx: AppContext, user: CurrentUser, is_adult: bool) -> None:
    """Purpose: record the age question. Under-18s cannot use self-service search."""
    with ctx.sessions() as session, session.begin():
        row = session.get(User, user.user_id)
        if row is None:
            raise SignInError("account not found")
        row.is_minor = not is_adult
        row.age_answered_at = datetime.now(UTC)
    audit_global(ctx, user.actor, "auth.age_answered", {"adult": is_adult})


def sign_out(ctx: AppContext, user: CurrentUser, everywhere: bool) -> int:
    """Purpose: end this session, or all of the user's sessions. Outputs: sessions ended."""
    with ctx.sessions() as session, session.begin():
        count = revoke_sessions(
            session, user.user_id, session_id=None if everywhere else user.session_id
        )
    audit_global(
        ctx,
        user.actor,
        "auth.signed_out_everywhere" if everywhere else "auth.signed_out",
        {"sessions": count},
    )
    return count
