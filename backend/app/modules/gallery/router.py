"""HTTP routes for the guest gallery and downloads (the guest's own matches only)."""

from uuid import UUID

from fastapi import APIRouter, Response

from app.core.deps import Ctx, User
from app.modules.gallery import service
from app.modules.gallery.schemas import GalleryPhoto, GalleryResponse

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
