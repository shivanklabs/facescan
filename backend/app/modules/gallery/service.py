"""Guest gallery: the guest's own released photos only.

Signed URLs (web-size copies, EXIF-free) are issued only after the permission check: the
caller is a guest of the event with a current consent, and the photo is one of *their*
matches. Downloads go through the same check and return a full-resolution copy with all
metadata stripped, re-encoded in memory. Organisers and photographers have no route to
anyone's matches (they never see who matched which photo).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.core.context import AppContext
from app.core.deps import require_role
from app.core.errors import NotFoundError
from app.db.event_scope import event_transaction
from app.modules.audit.service import record
from app.modules.consent.service import FACE_SEARCH, active_consent
from app.modules.gallery.schemas import ClaimNotice
from app.modules.ingest.models import Photo
from app.modules.matching.models import Match


@dataclass(frozen=True, slots=True)
class GalleryItem:
    """One released photo: a short-lived signed URL of its web copy."""

    photo_id: UUID
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GalleryContent:
    """The guest's released photos and rejected claim notices."""

    photos: list[GalleryItem]
    rejections: list[ClaimNotice]


def _guest_with_consent(session: Session, event_id: UUID, user: CurrentUser) -> None:
    """Purpose: the permission check shared by gallery and download. Raises 403/404."""
    # Lighter check (guest role plus active consent) because viewing released photos
    # must keep working after the search window closes.
    require_role(session, event_id, user.user_id, ("guest",))
    if active_consent(session, event_id, user.user_id, FACE_SEARCH) is None:
        raise HTTPException(status_code=403, detail="consent_required")


def get_gallery_content(ctx: AppContext, user: CurrentUser, event_id: UUID) -> GalleryContent:
    """
    Purpose: retrieve guest's released photos and claim notices under event scope.
    Inputs: ctx, user, event_id.
    Outputs: GalleryContent with photo items and rejections.
    Side effects: writes gallery.viewed audit record.
    """
    bucket = ctx.settings.storage_bucket_derivatives
    with event_transaction(ctx.sessions, event_id) as session:
        _guest_with_consent(session, event_id, user)
        rows = session.execute(
            select(Photo.id, Photo.web_object_key)
            .join(Match, Match.photo_id == Photo.id)
            .where(
                Match.event_id == event_id,
                Match.user_id == user.user_id,
                Match.score_band == "release",
                Photo.web_object_key.is_not(None),
            )
            .order_by(Photo.created_at)
        ).all()
        items = []
        for photo_id, web_key in rows:
            signed = ctx.storage.create_signed_download_url(bucket, event_id, str(web_key))
            items.append(GalleryItem(photo_id, signed.url, signed.expires_at))

        from app.modules.gallery.models import Claim

        claims_rejected = session.scalars(
            select(Claim)
            .where(
                Claim.event_id == event_id,
                Claim.user_id == user.user_id,
                Claim.status == "rejected",
            )
            .order_by(Claim.reviewed_at.desc())
        ).all()
        rejections = [
            ClaimNotice(
                claim_id=c.id,
                photo_id=c.photo_id,
                status=c.status,
                reason=c.reason,
                reviewed_at=c.reviewed_at,
            )
            for c in claims_rejected
        ]

        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="gallery.viewed",
            details={"photos": len(items), "rejections": len(rejections)},
        )
        return GalleryContent(photos=items, rejections=rejections)


def download(ctx: AppContext, user: CurrentUser, event_id: UUID, photo_id: UUID) -> bytes:
    """
    Purpose: full-resolution, metadata-free copy of one of the guest's released photos.
    Raises 404 unless the photo is the caller's own match. Side effects: audit row.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        _guest_with_consent(session, event_id, user)
        row = session.execute(
            select(Photo.bucket, Photo.object_key)
            .join(Match, Match.photo_id == Photo.id)
            .where(
                Match.event_id == event_id,
                Match.user_id == user.user_id,
                Match.photo_id == photo_id,
                Match.score_band == "release",
            )
        ).first()
        if row is None:
            raise NotFoundError("photo not found")
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="gallery.downloaded",
            target=str(photo_id),
        )
        bucket, key = row
    return ctx.analyzer.full_copy(ctx.storage.get_object(bucket, event_id, key))


def mark_not_me(ctx: AppContext, user: CurrentUser, event_id: UUID, photo_id: UUID) -> None:
    """
    Purpose: mark a matched photo as 'not me' for a guest.
    Inputs: ctx, user, event_id, photo_id.
    Outputs: None.
    Side effects: updates Match.score_band to 'not_me', inserts CannotLink rows,
        records gallery.not_me in audit_log.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        _guest_with_consent(session, event_id, user)
        match = session.scalars(
            select(Match).where(
                Match.event_id == event_id,
                Match.user_id == user.user_id,
                Match.photo_id == photo_id,
            )
        ).first()
        if match is None or match.score_band not in ("release", "borderline"):
            raise NotFoundError("photo not found in your gallery")

        match.score_band = "not_me"

        from app.modules.gallery.models import Claim
        from app.modules.matching.models import CannotLink
        from app.modules.vision_pipeline.models import Face

        claimed_face_id = session.scalar(
            select(Claim.face_id).where(
                Claim.event_id == event_id,
                Claim.user_id == user.user_id,
                Claim.photo_id == photo_id,
                Claim.face_id.is_not(None),
            )
        )

        faces = session.scalars(
            select(Face).where(Face.event_id == event_id, Face.photo_id == photo_id)
        ).all()

        if claimed_face_id is not None:
            session.add(
                CannotLink(
                    event_id=event_id,
                    user_id=user.user_id,
                    photo_id=photo_id,
                    face_id=claimed_face_id,
                )
            )
        elif faces:
            for f in faces:
                session.add(
                    CannotLink(
                        event_id=event_id,
                        user_id=user.user_id,
                        photo_id=photo_id,
                        face_id=f.id,
                    )
                )
        else:
            session.add(
                CannotLink(
                    event_id=event_id,
                    user_id=user.user_id,
                    photo_id=photo_id,
                    face_id=None,
                )
            )

        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="gallery.not_me",
            target=str(photo_id),
            details={"photo_id": str(photo_id)},
        )
