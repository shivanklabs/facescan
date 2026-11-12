"""Tables: users, passkeys, sessions, magic_links, auth_challenges (global, not event-scoped).

These tables hold only hashes and encrypted values: email addresses are stored as an HMAC
(lookup) and AES-GCM ciphertext; session and magic-link tokens only as SHA-256 hashes.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.columns import created_at, uuid_pk


class User(Base):
    """A person account. The email is stored only hashed (lookup) and encrypted (contact)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_minor: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    age_answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class Passkey(Base):
    """A WebAuthn credential."""

    __tablename__ = "passkeys"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """A signed-in session. The cookie holds a random token; only its SHA-256 is stored.
    event_id is set when the session was opened from an event invite (withdrawing consent
    for that event ends it)."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "auth_method IN ('magic_link', 'passkey', 'google', 'dev')", name="auth_method"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MagicLink(Base):
    """A single-use sign-in link. Only the SHA-256 of its token is stored."""

    __tablename__ = "magic_links"

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    email_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, index=True)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    next_path: Mapped[str] = mapped_column(String(200), nullable=False, server_default="/")
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthChallenge(Base):
    """A WebAuthn challenge (registration or authentication), single use, short-lived."""

    __tablename__ = "auth_challenges"
    __table_args__ = (CheckConstraint("purpose IN ('register', 'authenticate')", name="purpose"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
