"""Sign-in: passkeys (WebAuthn) and staff keys, plus sessions and the age answer.

- Magic links: random token, only its SHA-256 stored, single use (atomic UPDATE ... WHERE
  used_at IS NULL), expire after FACESCAN_AUTH_MAGIC_LINK_MINUTES (15). Requests are capped
  per address per hour. The response never reveals whether an account exists.
- Passkeys: py_webauthn; challenges stored server-side, single use, 5 minutes. Organisers and
  photographers must use a passkey; guests are offered one after their first sign-in.
- Every sign-in creates a new session (rotation) and revokes the one it replaces.
"""

import hashlib
import secrets
import uuid
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

from app.core.errors import ForbiddenError, UnprocessableError
from app.core.login_sessions import (
    CurrentUser,
    admin_allowed,
    create_anonymous_user,
    create_session,
    find_or_create_user,
    normalise_email,
    organiser_allowed,
    revoke_sessions,
)
from app.core.request_checks import require_role
from app.core.services_setup import AppContext
from app.db.event_isolation import (
    ADMIN_SCOPE,
    event_transaction,
    scoped_transaction,
    user_transaction,
)
from app.features.activity_log.service import record
from app.features.events.models import EventMember
from app.features.login.models import AuthChallenge, Passkey, StaffKey, User

CHALLENGE_MINUTES = 5


class SignInError(Exception):
    """A sign-in attempt failed (message is generic on purpose)."""


@dataclass(frozen=True, slots=True)
class SignInResult:
    """A new session and where to go next."""

    token: str
    user_id: UUID
    next_path: str
    has_passkey: bool


def audit_global(
    ctx: AppContext,
    actor: str,
    action: str,
    details: dict[str, int | str | bool | None] | None = None,
) -> None:
    """Purpose: audit row without an event (admin scope). Side effects: one insert."""
    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as session:
        record(session, event_id=None, actor=actor, action=action, details=details)


def dev_login_allowed(ctx: AppContext) -> bool:
    """
    Purpose: the email-only dev sign-in exists only in local development (FACESCAN_ENV=dev).
    Inputs: ctx. Outputs: bool. Side effects: none.
    """
    return ctx.settings.env == "dev"


def sign_in_dev(ctx: AppContext, address: str, current_session: UUID | None) -> SignInResult:
    """
    Purpose: local-development sign-in with an email address only (testing without a
        passkey device). Refused outside FACESCAN_ENV=dev.
    Inputs: ctx, email address, the session it replaces.
    Outputs: SignInResult (next path "/").
    Raises ForbiddenError outside dev, UnprocessableError for a malformed address.
    Side effects: creates the user on first sign-in; rotates the session; audit row (no email).
    """
    if not dev_login_allowed(ctx):
        raise ForbiddenError("dev sign-in is only available in development")
    try:
        email = normalise_email(address)
    except ValueError as exc:
        raise UnprocessableError("enter a valid email address") from exc
    with ctx.sessions() as session, session.begin():
        user = find_or_create_user(session, ctx.keys, email)
        has_passkey = bool(
            session.scalar(
                select(func.count()).select_from(Passkey).where(Passkey.user_id == user.id)
            )
        )
        token = create_session(
            session, ctx.settings, user.id, "dev", None, replaces=current_session
        )
        user_id = user.id
    audit_global(ctx, f"user:{user_id}", "auth.signed_in", {"method": "dev"})
    return SignInResult(token, user_id, "/", has_passkey)


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


# ---------------------------------------------------------------- guests: passkey only


def guest_registration_options(ctx: AppContext) -> str:
    """
    Purpose: WebAuthn creation options for a new guest (no email, no account yet). The
        account id is chosen now but the account is created only when the passkey verifies.
    Inputs: ctx. Outputs: options JSON.
    Side effects: stores one single-use challenge with the pending account id.
    """
    pending = uuid.uuid4()
    challenge = secrets.token_bytes(32)
    options = generate_registration_options(
        rp_id=ctx.settings.auth_rp_id,
        rp_name=ctx.settings.auth_rp_name,
        user_id=pending.bytes,
        user_name=f"guest-{str(pending)[:8]}",  # no personal data
        challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    with ctx.sessions() as session, session.begin():
        session.add(
            AuthChallenge(
                challenge=challenge,
                purpose="guest_register",
                user_id=None,
                pending_user_id=pending,
                expires_at=datetime.now(UTC) + timedelta(minutes=CHALLENGE_MINUTES),
            )
        )
    return options_to_json(options)


def sign_up_guest_with_passkey(
    ctx: AppContext,
    credential: dict[str, object],
    challenge: bytes,
    current_session: UUID | None,
) -> SignInResult:
    """
    Purpose: verify a new guest's passkey, then create the account (anonymous: no email) and a
        passkey session. Nothing is created if the passkey does not verify.
    Inputs: ctx, the WebAuthn credential, its challenge, the session it replaces.
    Outputs: SignInResult. Raises SignInError.
    Side effects: uses the challenge once; inserts user, passkey, session; audit row.
    """
    now = datetime.now(UTC)
    with ctx.sessions() as session, session.begin():
        pending = session.execute(
            update(AuthChallenge)
            .where(
                AuthChallenge.challenge == challenge,
                AuthChallenge.purpose == "guest_register",
                AuthChallenge.used_at.is_(None),
                AuthChallenge.expires_at > now,
            )
            .values(used_at=now)
            .returning(AuthChallenge.pending_user_id)
        ).scalar()
    if pending is None:
        raise SignInError("sign-up expired; try again")
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
        user = create_anonymous_user(session, ctx.keys, pending)
        session.add(
            Passkey(
                user_id=user.id,
                credential_id=verified.credential_id,
                public_key=verified.credential_public_key,
                sign_count=verified.sign_count,
            )
        )
        token = create_session(
            session, ctx.settings, user.id, "passkey", None, replaces=current_session
        )
        user_id = user.id
    audit_global(ctx, f"user:{user_id}", "auth.signed_up", {"method": "passkey"})
    return SignInResult(token, user_id, "/", True)


# ---------------------------------------------------------------- staff keys (no email)

STAFF_KEY_DAYS = 7
STAFF_KEY_PREFIX = "FS-"
STAFF_ROLES_BY_ADMIN = ("admin", "organiser")


@dataclass(frozen=True, slots=True)
class StaffKeyView:
    """A staff key as listed to its creator (never the key or its hash)."""

    key_id: UUID
    role: str
    event_id: UUID | None
    created_at: datetime
    expires_at: datetime
    used: bool
    revoked: bool


def _hash_staff_key(key: str) -> bytes:
    """Purpose: SHA-256 of a staff key (only the hash is stored). Outputs: 32 bytes."""
    return hashlib.sha256(key.strip().encode("utf-8")).digest()


def _key_view(row: StaffKey) -> StaffKeyView:
    """Purpose: ORM row -> view."""
    return StaffKeyView(
        row.id,
        row.role,
        row.event_id,
        row.created_at,
        row.expires_at,
        row.used_at is not None,
        row.revoked_at is not None,
    )


def create_staff_key(
    ctx: AppContext, creator: CurrentUser | None, role: str, event_id: UUID | None
) -> tuple[str, StaffKeyView]:
    """
    Purpose: generate a one-time staff key. Admins may create admin and organiser keys;
        organisers of an event may create photographer keys for that event; creator None is
        the server owner at the terminal (bootstrap).
    Inputs: ctx, the creator (None = terminal), role, event_id (photographer keys only).
    Outputs: (the key, shown once; its view). Raises ForbiddenError / UnprocessableError.
    Side effects: inserts a staff_keys row (hash only); audit row.
    """
    if role not in ("admin", "organiser", "photographer"):
        raise UnprocessableError("unknown role")
    if (role == "photographer") != (event_id is not None):
        raise UnprocessableError("photographer keys need an event; other roles must not have one")
    if creator is not None:
        if role in STAFF_ROLES_BY_ADMIN:
            with ctx.sessions() as session:
                row = session.get(User, creator.user_id)
                if row is None or not admin_allowed(row):
                    raise ForbiddenError("only platform admins can create this key")
        else:
            assert event_id is not None
            with event_transaction(ctx.sessions, event_id) as session:
                require_role(session, event_id, creator.user_id, ("organiser",))
    key = STAFF_KEY_PREFIX + secrets.token_urlsafe(24)
    with ctx.sessions() as session, session.begin():
        staff_key = StaffKey(
            key_hash=_hash_staff_key(key),
            role=role,
            event_id=event_id,
            created_by=creator.user_id if creator else None,
            expires_at=datetime.now(UTC) + timedelta(days=STAFF_KEY_DAYS),
        )
        session.add(staff_key)
        session.flush()
        view = _key_view(staff_key)
    actor = creator.actor if creator else "system:terminal"
    audit_global(ctx, actor, "auth.staff_key_created", {"role": role})
    return key, view


def list_staff_keys(ctx: AppContext, creator: CurrentUser) -> list[StaffKeyView]:
    """Purpose: the staff keys this person created, newest first (no keys or hashes)."""
    with ctx.sessions() as session:
        rows = session.scalars(
            select(StaffKey)
            .where(StaffKey.created_by == creator.user_id)
            .order_by(StaffKey.created_at.desc())
            .limit(100)
        ).all()
        return [_key_view(r) for r in rows]


def revoke_staff_key(ctx: AppContext, creator: CurrentUser, key_id: UUID) -> None:
    """
    Purpose: turn off an unused key the person created. Raises UnprocessableError if it is
        not theirs or already used. Side effects: sets revoked_at; audit row.
    """
    with ctx.sessions() as session, session.begin():
        row = session.get(StaffKey, key_id)
        if row is None or row.created_by != creator.user_id or row.used_at is not None:
            raise UnprocessableError("key not found or already used")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
    audit_global(ctx, creator.actor, "auth.staff_key_revoked")


def redeem_staff_key(ctx: AppContext, key: str, current: CurrentUser | None) -> SignInResult | None:
    """
    Purpose: use a staff key once. A signed-in person gets the role on their own account;
        otherwise a new account (no email) is created with a short email-link-strength
        session, and the site then asks them to add a passkey.
    Inputs: ctx, the key, the current user (or None).
    Outputs: SignInResult when a new account/session was made, else None.
    Raises SignInError for an unknown, used, revoked or expired key (one message).
    Side effects: marks the key used; sets user flags or adds an event membership; may insert
        user + session; audit row.
    """
    now = datetime.now(UTC)
    with ctx.sessions() as session, session.begin():
        row = session.scalars(
            select(StaffKey)
            .where(
                StaffKey.key_hash == _hash_staff_key(key),
                StaffKey.used_at.is_(None),
                StaffKey.revoked_at.is_(None),
                StaffKey.expires_at > now,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise SignInError("staff key not valid")
        result: SignInResult | None = None
        if current is None:
            user = create_anonymous_user(session, ctx.keys)
            token = create_session(session, ctx.settings, user.id, "magic_link", None)
            result = SignInResult(token, user.id, "/studio", False)
            user_id = user.id
        else:
            user_id = current.user_id
            account = session.get(User, user_id)
            if account is None:
                raise SignInError("staff key not valid")
            user = account
        if row.role == "admin":
            user.is_admin = True
            user.can_create_events = True
        elif row.role == "organiser":
            user.can_create_events = True
        row.used_at = now
        row.used_by = user_id
        role, event_id = row.role, row.event_id
    if role == "photographer" and event_id is not None:
        with event_transaction(ctx.sessions, event_id) as session:
            exists = session.scalar(
                select(EventMember.event_id).where(
                    EventMember.event_id == event_id,
                    EventMember.user_id == user_id,
                    EventMember.role == "photographer",
                )
            )
            if exists is None:
                session.add(EventMember(event_id=event_id, user_id=user_id, role="photographer"))
            record(session, event_id=event_id, actor=f"user:{user_id}", action="staff.joined")
    audit_global(ctx, f"user:{user_id}", "auth.staff_key_used", {"role": role})
    return result


def issue_staff_key_from_terminal(role: str) -> str:
    """
    Purpose: bootstrap: the server owner creates the first admin (or organiser) key at the
        terminal (uv run tasks staff-key). Inputs: role. Outputs: the key (printed once).
    Side effects: builds an app context from settings; inserts one staff key.
    """
    from app.core.services_setup import build_context  # local: terminal use only
    from app.core.settings import get_settings

    ctx = build_context(get_settings())
    key, _ = create_staff_key(ctx, None, role, None)
    return key


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
    is_staff: bool = False  # organiser or photographer of at least one event


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
            can_create_events=organiser_allowed(row),
            is_admin=admin_allowed(row),
            is_staff=_is_staff(ctx, user.user_id),
        )


def _is_staff(ctx: AppContext, user_id: UUID) -> bool:
    """
    Purpose: whether the user is an organiser or photographer of any event (so the site shows
        them Studio). Inputs: ctx, user_id. Outputs: bool.
    Side effects: one indexed lookup in the user's own read-only scope.
    """
    with user_transaction(ctx.sessions, user_id) as session:
        return (
            session.scalar(
                select(EventMember.event_id)
                .where(
                    EventMember.user_id == user_id,
                    EventMember.role.in_(("organiser", "photographer")),
                )
                .limit(1)
            )
            is not None
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
