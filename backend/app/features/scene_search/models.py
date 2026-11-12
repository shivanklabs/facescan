"""SQLAlchemy models for scene search, candidate tracking, claims and rate limits."""

import uuid
from datetime import datetime

from app.db.base import Base
from app.db.columns import created_at, event_fk, uuid_pk
from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class SceneEmbedding(Base):
    """Encrypted 768-dim SigLIP 2 scene vector for one photo of an event."""

    __tablename__ = "scene_embeddings"

    photo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[uuid.UUID] = event_fk()
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_at()


class SceneSearchCandidate(Base):
    """Short-lived server-side record of candidate faces shown to a guest in scene search."""

    __tablename__ = "scene_search_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    photo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("photos.id", ondelete="CASCADE"), nullable=False
    )
    face_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faces.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at()


class SceneSearchAttempt(Base):
    """Rate limiting attempt row for scene search per guest per event per day."""

    __tablename__ = "scene_search_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at()


class ClaimAttempt(Base):
    """Rate limiting attempt row for claims per guest per event per day."""

    __tablename__ = "claim_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = event_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at()
