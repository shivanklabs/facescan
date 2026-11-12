"""HTTP routes for the guest gallery and downloads (the guest's own matches only)."""

from uuid import UUID

from fastapi import APIRouter, Response

from app.core.request_checks import Ctx, User
from app.features.gallery import service
from app.features.gallery.schemas import GalleryPhoto, GalleryResponse

router = APIRouter(prefix="/events/{event_id}/gallery", tags=["gallery"])


@router.get("", response_model=GalleryResponse)
def gallery(event_id: UUID, user: User, ctx: Ctx) -> GalleryResponse:
    """Purpose: the guest's released photos and claim rejection notices."""
    content = service.get_gallery_content(ctx, user, event_id)
    return GalleryResponse(
        photos=[
            GalleryPhoto(
                photo_id=item.photo_id,
                url=item.url,
                expires_at=item.expires_at,
                download_path=f"/events/{event_id}/gallery/{item.photo_id}/download",
            )
            for item in content.photos
        ],
        rejections=content.rejections,
    )


@router.post("/{photo_id}/not-me")
def not_me(event_id: UUID, photo_id: UUID, user: User, ctx: Ctx) -> dict[str, str]:
    """
    Purpose: mark a photo as 'not me' for the guest, immediately removing it from gallery
        and preventing future release or claims.
    Inputs: event_id, photo_id, user, ctx.
    Outputs: {"status": "ok", "photo_id": str(photo_id)}.
    Side effects: updates match to not_me, inserts cannot_links, writes audit row.
    """
    service.mark_not_me(ctx, user, event_id, photo_id)
    return {"status": "ok", "photo_id": str(photo_id)}


@router.get("/{photo_id}/download")
def download(event_id: UUID, photo_id: UUID, user: User, ctx: Ctx) -> Response:
    """
    Purpose: full-resolution, metadata-safe copy of one of the guest's photos.
    Inputs: event_id, photo_id, user, ctx.
    Outputs: Response with image bytes and attachment headers.
    Side effects: queries database and storage.
    """
    data = service.download(ctx, user, event_id, photo_id)
    content_type = "image/jpeg"
    ext = "jpg"
    if data.startswith(b"\x89PNG"):
        content_type = "image/png"
        ext = "png"
    elif data.startswith(b"RIFF") and b"WEBP" in data[:12]:
        content_type = "image/webp"
        ext = "webp"
    return Response(
        data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="facescan-{photo_id}.{ext}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
