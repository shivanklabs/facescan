"""TEMPORARY DEV-ONLY match endpoint (dev admin token). Real guest search comes with login.

POST /events/{event_id}/match takes the selfie as the raw request body (Content-Type
image/jpeg, image/png or image/webp), read straight into memory with a size cap, so it is
never spooled to a temporary file (invariant 3).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.errors import PayloadTooLargeError, UnprocessableError
from app.core.rate_limit import RateLimiter
from app.core.security import require_dev_admin
from app.modules.events.router import Ctx
from app.modules.matching import service
from app.modules.matching.schemas import MatchBandsResponse

router = APIRouter(prefix="/events/{event_id}", tags=["match (dev token)"])
Actor = Annotated[str, Depends(require_dev_admin)]
match_limiter = RateLimiter("match", limit=10, window_seconds=60)
SELFIE_TYPES = ("image/jpeg", "image/png", "image/webp")


async def read_selfie(request: Request, max_bytes: int) -> bytes:
    """
    Purpose: read the raw selfie body into memory, never more than max_bytes.
    Raises PayloadTooLargeError or UnprocessableError. Side effects: reads the body.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in SELFIE_TYPES:
        raise UnprocessableError("send the selfie as image/jpeg, image/png or image/webp")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > max_bytes:
        raise PayloadTooLargeError("selfie is larger than allowed")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLargeError("selfie is larger than allowed")
        chunks.append(chunk)
    if size == 0:
        raise UnprocessableError("empty selfie")
    return b"".join(chunks)


@router.post(
    "/match",
    response_model=MatchBandsResponse,
    dependencies=[Depends(match_limiter)],
)
async def match(event_id: UUID, request: Request, actor: Actor, ctx: Ctx) -> MatchBandsResponse:
    """Purpose: photo ids per band for a one-face selfie (dev only)."""
    selfie = await read_selfie(request, ctx.settings.selfie_max_bytes)
    bands = service.dev_match(ctx, event_id, selfie, actor)
    del selfie
    return MatchBandsResponse(event_id=event_id, release=bands.release, borderline=bands.borderline)
