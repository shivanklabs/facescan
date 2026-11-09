"""Table: photos (uploaded event photos and their web-size copies)."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.columns import created_at, event_fk, uuid_pk

PHOTO_STATUSES = ("quarantined", "approved", "indexed", "failed")


class Photo(Base):
    """One uploaded photo. bucket/object_key point at the original (quarantine until approved,
    then the photos bucket); web_object_key at the EXIF-free web copy."""

    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint(f"status IN {PHOTO_STATUSES}", name="status"),
        CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')", name="content_type"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_sections.id", ondelete="SET NULL")
    )
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    object_key: Mapped[str] = mapped_column(String(100), nullable=False)
    web_object_key: Mapped[str | None] = mapped_column(String(100))
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="quarantined")
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
