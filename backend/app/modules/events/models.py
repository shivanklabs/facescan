"""Tables: events, event_members, event_sections, event_keys, collections."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.columns import created_at, event_fk, uuid_pk

EVENT_STATUSES = ("draft", "indexing", "searchable", "failed", "closed")
COLLECTION_STATUSES = ("open", "approved")


class Event(Base):
    """An event. event_id always equals id, so the generic RLS policy applies here too."""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("event_id = id", name="event_id_is_id"),
        CheckConstraint(f"status IN {EVENT_STATUSES}", name="status"),
        CheckConstraint("char_length(country) = 2", name="country"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_window_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class EventMember(Base):
    """Role of a user in an event (organiser, photographer, guest)."""

    __tablename__ = "event_members"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", "role"),
        CheckConstraint("role IN ('organiser', 'photographer', 'guest')", name="role"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = created_at()


class EventSection(Base):
    """Sub-area or day of an event."""

    __tablename__ = "event_sections"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = created_at()


class EventKey(Base):
    """An event's data key, wrapped by the master key. NULL wrapped_key = destroyed."""

    __tablename__ = "event_keys"
    __table_args__ = (
        UniqueConstraint("event_id", "key_version"),
        CheckConstraint(
            "(wrapped_key IS NULL) = (destroyed_at IS NOT NULL)", name="destroyed_has_no_key"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    master_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_at()
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Collection(Base):
    """A batch of uploaded photos; approving it releases the photos for indexing."""

    __tablename__ = "collections"
    __table_args__ = (CheckConstraint(f"status IN {COLLECTION_STATUSES}", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    created_at: Mapped[datetime] = created_at()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
