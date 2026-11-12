"""HTTP routes for discover (milestone 7): scan once, search every event; see all my photos.

POST /discover/search takes 3-7 live frames (front, a burst while blinking, front) in the same
length-prefixed body as the per-event search (Content-Type application/x-facescan-frames);
frames stay in memory (invariant 3).
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.core.deps import Ctx, User
from app.features.all_events_search import service
from app.features.all_events_search.schemas import (
    DiscoverEvent,
    DiscoverPhoto,
    DiscoverPhotosResponse,
    DiscoverSearchResponse,
)
from app.features.face_matching.router import read_frames, search_limiter

router = APIRouter(prefix="/discover", tags=["discover"])


@router.post(
    "/search", response_model=DiscoverSearchResponse, dependencies=[Depends(search_limiter)]
)
async def search(
    request: Request,
    user: User,
    ctx: Ctx,
    notice_version: str = Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$"),
    method: Literal["blink", "look"] = Query(default="blink"),
) -> DiscoverSearchResponse:
    """
    Purpose: one live scan (selfie + blink) searched in every searchable event.
    Inputs: frames body, signed-in user, the notice version the person agreed to, scan method
        ("blink" by default; "look" skips the blink challenge and works only in development).
    Outputs: DiscoverSearchResponse (counts only).
    Side effects: see service.search_all_events (runs in the threadpool).
    """
    frames = await read_frames(
        request, ctx.settings.discover_max_frames, ctx.settings.selfie_max_bytes
    )
    result = await run_in_threadpool(
        service.search_all_events, ctx, user, frames, notice_version, method == "blink"
    )
    return DiscoverSearchResponse(
        released=result.released,
        events_with_photos=result.events_with_photos,
        events_searched=result.events_searched,
    )


@router.get("/photos", response_model=DiscoverPhotosResponse)
async def photos(user: User, ctx: Ctx) -> DiscoverPhotosResponse:
    """
    Purpose: all of the signed-in person's released photos, grouped by event.
    Inputs: user, ctx. Outputs: DiscoverPhotosResponse with signed URLs.
    Side effects: gallery.viewed audit rows.
    """
    groups = await run_in_threadpool(service.my_photos, ctx, user)
    return DiscoverPhotosResponse(
        events=[
            DiscoverEvent(
                event_id=group.event_id,
                event_name=group.event_name,
                photos=[
                    DiscoverPhoto(
                        photo_id=item.photo_id,
                        url=item.url,
                        expires_at=item.expires_at,
                        download_path=(
                            f"/events/{group.event_id}/gallery/{item.photo_id}/download"
                        ),
                    )
                    for item in group.photos
                ],
            )
            for group in groups
        ]
    )
