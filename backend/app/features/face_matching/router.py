"""Guest face search.

POST /events/{event_id}/search takes 3-5 live selfie frames as one raw body
(Content-Type application/x-facescan-frames): each frame is a 4-byte big-endian length
followed by that many bytes of JPEG/PNG/WebP. The body is read straight into memory with size
caps, so frames are never spooled to a temporary file (invariant 3).
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.core.errors import PayloadTooLargeError, UnprocessableError
from app.core.rate_limit import RateLimiter
from app.core.request_checks import Ctx, User
from app.features.face_matching import service
from app.features.face_matching.schemas import SearchResponse

router = APIRouter(prefix="/events/{event_id}", tags=["guest search"])
search_limiter = RateLimiter("search", limit=30, window_seconds=60)
FRAMES_CONTENT_TYPE = "application/x-facescan-frames"
LENGTH_BYTES = 4


def split_frames(body: bytes, max_frames: int, max_frame_bytes: int) -> list[bytes]:
    """
    Purpose: parse the length-prefixed frames. Raises UnprocessableError or
        PayloadTooLargeError. Side effects: none.
    """
    frames: list[bytes] = []
    offset = 0
    while offset < len(body):
        if len(frames) >= max_frames:
            raise UnprocessableError(f"at most {max_frames} frames")
        if offset + LENGTH_BYTES > len(body):
            raise UnprocessableError("malformed frames body")
        size = int.from_bytes(body[offset : offset + LENGTH_BYTES], "big")
        offset += LENGTH_BYTES
        if size == 0 or size > max_frame_bytes:
            raise PayloadTooLargeError("a frame is empty or larger than allowed")
        if offset + size > len(body):
            raise UnprocessableError("malformed frames body")
        frames.append(body[offset : offset + size])
        offset += size
    return frames


async def read_frames(request: Request, max_frames: int, max_frame_bytes: int) -> list[bytes]:
    """Purpose: read the raw body into memory (size-capped) and split it into frames."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != FRAMES_CONTENT_TYPE:
        raise UnprocessableError(f"send the frames as {FRAMES_CONTENT_TYPE}")
    limit = max_frames * (max_frame_bytes + LENGTH_BYTES)
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        raise PayloadTooLargeError("selfie frames are larger than allowed")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError("selfie frames are larger than allowed")
        chunks.append(chunk)
    return split_frames(b"".join(chunks), max_frames, max_frame_bytes)


@router.post("/search", response_model=SearchResponse, dependencies=[Depends(search_limiter)])
async def search(
    event_id: UUID,
    request: Request,
    user: User,
    ctx: Ctx,
    mode: Literal["guided", "front-only"] = Query(default="guided"),
) -> SearchResponse:
    """
    Purpose: the guest's photos in this event (release band only) offloaded to threadpool.
    Inputs: event_id, request with selfie frames, user, ctx, mode.
    Outputs: SearchResponse with release photos and missing photos flag.
    Side effects: updates guest matches and search attempt record.
    """
    frames = await read_frames(
        request, ctx.settings.search_max_frames, ctx.settings.selfie_max_bytes
    )
    result = await run_in_threadpool(
        service.guest_search, ctx, user, event_id, frames, front_only=(mode == "front-only")
    )
    return SearchResponse(release=result.release, some_photos_missing=result.some_photos_missing)
