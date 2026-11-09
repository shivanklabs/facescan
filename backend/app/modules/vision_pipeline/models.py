"""Tables: faces, clusters, event_calibrations.

Biometric data (embeddings, landmarks, prototypes), cluster membership and calibration are
stored only as AES-GCM ciphertext under the event's data key (invariant 4); destroying the key
makes them unreadable. Plain columns hold no biometric data: box geometry, quality, weak flag.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, LargeBinary, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.columns import created_at, event_fk, uuid_pk


class Cluster(Base):
    """A group of faces of one person within an event. payload_encrypted holds the prototypes
    and the member / attached face ids."""

    __tablename__ = "clusters"
    __table_args__ = (UniqueConstraint("event_id", "cluster_no"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    cluster_no: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at()


class Face(Base):
    """A detected face. embedding_encrypted holds both engines' vectors and the landmarks."""

    __tablename__ = "faces"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    photo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("photos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bbox_x: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_w: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_h: Mapped[float] = mapped_column(Float, nullable=False)
    detector_score: Mapped[float] = mapped_column(Float, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)
    weak: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    embedding_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at()


class EventCalibration(Base):
    """Per-event thresholds (encrypted JSON) and the engine versions they belong to."""

    __tablename__ = "event_calibrations"
    __table_args__ = (UniqueConstraint("event_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at()
