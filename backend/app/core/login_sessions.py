"""Sign-in primitives: token hashing, email protection and server-side sessions.

- Tokens (sessions, staff keys, invite secrets) are random; only their SHA-256 is stored.
- Email addresses are stored as an HMAC (for lookup) and AES-GCM ciphertext (for contact),
  each under a key derived from the master key through the KeyManager port.
- Sessions live in PostgreSQL (sessions table). The cookie is HttpOnly, Secure and
  SameSite=Strict, and a new session (new token) is issued at every sign-in (rotation).
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.encryption import DecryptionError, seal
from app.core.settings import Settings
from app.features.login.models import AuthSession, User
from app.interfaces.encryption_keys import KeyManager

SESSION_COOKIE = "fs_session"
TOKEN_BYTES = 32
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}$")
_EMAIL_AAD = b"facescan-user-email|v1"


class InvalidEmailError(ValueError):
    """The text is not an email address."""


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The signed-in user of a request."""

    user_id: UUID
    session_id: UUID
    auth_method: str
    session_event_id: UUID | None

    @property
    def actor(self) -> str:
        """Audit actor (an id, never an email)."""
        return f"user:{self.user_id}"


def new_token() -> str:
    """Purpose: a random URL-safe token (256 bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    """Purpose: SHA-256 of a token (what the database stores)."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def normalise_email(address: str) -> str:
    """Purpose: trimmed, lower-case address. Raises InvalidEmailError."""
    value = address.strip().lower()
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise InvalidEmailError("not an email address")
    return value


def email_hash(keys: KeyManager, address: str) -> bytes:
    """Purpose: keyed hash of a normalised address (lookup without storing the address)."""
    return hmac.new(keys.derive_key("email-hash"), address.encode("utf-8"), hashlib.sha256).digest()


def seal_email(keys: KeyManager, address: str) -> bytes:
    """Purpose: encrypt an address for later contact."""
    return seal(keys.derive_key("email-encrypt"), address.encode("utf-8"), _EMAIL_AAD)


def create_anonymous_user(session: Session, keys: KeyManager, user_id: UUID | None = None) -> User:
    """
    Purpose: Create an anonymous guest account with a random UUID and synthetic address.
    Inputs:
        session: active SQLAlchemy database session
        keys: KeyManager for HMAC and encryption
        user_id: the id to use (the one a passkey was just created for), else a new one
    Outputs:
        User: the newly created user record
    Side Effects:
        Inserts a new User into the database and flushes.
    """
    anon_id = secrets.token_hex(16)
    anon_addr = f"anon_{anon_id}@guest.local"
    digest = email_hash(keys, anon_addr)
    user = User(
        email_hash=digest,
        email_encrypted=seal_email(keys, anon_addr),
        is_minor=False,
        age_answered_at=datetime.now(UTC),
    )
    if user_id is not None:
        user.id = user_id
    session.add(user)
    session.flush()
    return user


def find_or_create_user(session: Session, keys: KeyManager, address: str) -> User:
    """Purpose: the user for an address, created on first sign-in. Side effects: may insert."""
    digest = email_hash(keys, address)
    user = session.scalars(select(User).where(User.email_hash == digest)).first()
    if user is None:
        user = User(email_hash=digest, email_encrypted=seal_email(keys, address))
        session.add(user)
        session.flush()
    return user


def organiser_allowed(user: User) -> bool:
    """Purpose: True if the user may create events (set by a redeemed staff key)."""
    return user.can_create_events


def admin_allowed(user: User) -> bool:
    """Purpose: True if the user is a platform admin (set by a redeemed staff key)."""
    return user.is_admin


def create_session(
    session: Session,
    settings: Settings,
    user_id: UUID,
    auth_method: str,
    event_id: UUID | None,
    replaces: UUID | None = None,
) -> str:
    """
    Purpose: start a new session (rotation: the session it replaces is revoked).
    Outputs: the cookie token (only its hash is stored). Side effects: inserts/updates sessions.
    """
    now = datetime.now(UTC)
    if replaces is not None:
        session.execute(
            update(AuthSession)
            .where(AuthSession.id == replaces, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    token = new_token()
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user_id,
            event_id=event_id,
            auth_method=auth_method,
            expires_at=now + timedelta(hours=settings.auth_session_hours),
            last_seen_at=now,
        )
    )
    session.flush()
    return token


def lookup_session(session: Session, token: str) -> AuthSession | None:
    """Purpose: the live session for a cookie token (None if unknown, revoked or expired)."""
    row = session.scalars(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    ).first()
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        return None
    return row


def revoke_sessions(
    session: Session, user_id: UUID, *, session_id: UUID | None = None, event_id: UUID | None = None
) -> int:
    """
    Purpose: end one session, every session of the user for one event, or all of the user's
        sessions (when neither session_id nor event_id is given).
    Outputs: number revoked. Side effects: updates sessions.
    """
    query = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if session_id is not None:
        query = query.where(AuthSession.id == session_id)
    if event_id is not None:
        query = query.where(AuthSession.event_id == event_id)
    result = session.execute(query.values(revoked_at=datetime.now(UTC)))
    return int(getattr(result, "rowcount", 0) or 0)


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Purpose: HttpOnly, Secure, SameSite=Strict session cookie."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Purpose: remove the session cookie."""
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )


__all__ = [
    "SESSION_COOKIE",
    "CurrentUser",
    "DecryptionError",
    "InvalidEmailError",
    "admin_allowed",
    "clear_session_cookie",
    "create_anonymous_user",
    "create_session",
    "email_hash",
    "find_or_create_user",
    "hash_token",
    "lookup_session",
    "new_token",
    "normalise_email",
    "organiser_allowed",
    "revoke_sessions",
    "seal_email",
    "set_session_cookie",
]
