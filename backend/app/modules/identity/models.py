"""Tables: users, passkeys (global, not event-scoped). Used once login is built."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, LargeBinary
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
