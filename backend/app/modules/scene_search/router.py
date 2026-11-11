"""HTTP routes for scene search, blurred previews, and 1:1 claims."""

from typing import Annotated
from uuid import UUID

from app.core.deps import Ctx, User
from app.modules.scene_search import service
from app.modules.scene_search.schemas import (
    SceneSearchRequest,
    SceneSearchResponse,
    SubmitClaimRequest,
    SubmitClaimResponse,
)
from fastapi import APIRouter, Query, Response

router = APIRouter(prefix="/events/{event_id}/scene-search", tags=["scene search"])


@router.post("", response_model=SceneSearchResponse)
async def scene_search(
    event_id: UUID, req: SceneSearchRequest, user: User, ctx: Ctx
) -> SceneSearchResponse:
    """
    Purpose: natural language scene search over an event's photos using SigLIP 2 scene embeddings.
    Inputs: event_id, search request payload, authenticated user, app context.
    Outputs: SceneSearchResponse with candidate photos, blurred preview URLs, and detected faces.
    Side effects: records scene search attempts and saves candidate history.
    """
    return await service.search_scene(ctx, user, event_id, req)


@router.get("/photos/{photo_id}/preview")
def candidate_preview(
    event_id: UUID,
    photo_id: UUID,
    expires: Annotated[int, Query(ge=0)],
    signature: Annotated[str, Query(min_length=16, max_length=128)],
    user: User,
    ctx: Ctx,
) -> Response:
    """
    Purpose: serve a low-resolution strongly blurred preview of a candidate photo with
             all faces blurred.
    Inputs: event_id, photo_id, expires timestamp, HMAC signature, authenticated user, app context.
    Outputs: JPEG Response.
    Side effects: none.
    """
    preview_bytes = service.get_candidate_preview(
        ctx=ctx,
        user=user,
        event_id=event_id,
        photo_id=photo_id,
        expires=expires,
        signature=signature,
    )
    return Response(
        content=preview_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/claims", response_model=SubmitClaimResponse)
def submit_claim(
    event_id: UUID, req: SubmitClaimRequest, user: User, ctx: Ctx
) -> SubmitClaimResponse:
    """
    Purpose: submit 1:1 face claim on a candidate face using fresh guided selfie scan.
    Inputs: event_id, claim request payload with selfie frames, authenticated user, app context.
    Outputs: SubmitClaimResponse (released to gallery or queued for organiser review).
    Side effects: inserts Match or Claim row, records ClaimAttempt, writes audit log.
    """
    return service.submit_claim(ctx, user, event_id, req)
