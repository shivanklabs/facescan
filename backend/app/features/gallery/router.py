"""HTTP routes for a guest's own photos: "Not me" and full-size downloads (the list itself
comes from GET /discover/photos)."""

from uuid import UUID

from fastapi import APIRouter, Response

from app.core.request_checks import Ctx, User
from app.features.gallery import service

router = APIRouter(prefix="/events/{event_id}/gallery", tags=["gallery"])


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
