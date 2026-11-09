"""HTTP routes for the guest gallery and downloads (the guest's own matches only)."""

from uuid import UUID

from fastapi import APIRouter, Response

from app.core.deps import Ctx, User
from app.modules.gallery import service
from app.modules.gallery.schemas import GalleryPhoto, GalleryResponse

router = APIRouter(prefix="/events/{event_id}/gallery", tags=["gallery"])


@router.get("", response_model=GalleryResponse)
def gallery(event_id: UUID, user: User, ctx: Ctx) -> GalleryResponse:
    """Purpose: the guest's released photos as short-lived signed URLs."""
    return GalleryResponse(
        photos=[
            GalleryPhoto(
                photo_id=item.photo_id,
                url=item.url,
                expires_at=item.expires_at,
                download_path=f"/events/{event_id}/gallery/{item.photo_id}/download",
            )
            for item in service.gallery(ctx, user, event_id)
        ]
    )


@router.get("/{photo_id}/download")
def download(event_id: UUID, photo_id: UUID, user: User, ctx: Ctx) -> Response:
    """Purpose: full-resolution, metadata-free copy of one of the guest's photos."""
    data = service.download(ctx, user, event_id, photo_id)
    return Response(
        data,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="facescan-{photo_id}.jpg"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
