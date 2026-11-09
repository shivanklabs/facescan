"""HTTP routes for photo upload and collection approval.

TEMPORARY DEV-ONLY: protected by the dev admin token until login. Uploads are multipart
(field name "files", optional "collection_id"); the request size is checked from
Content-Length before the body is parsed. Starlette keeps parts up to 1 MB in memory and
spools larger event photos to a temporary file while parsing (never selfies: see matching).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app.core.errors import PayloadTooLargeError, UnprocessableError
from app.core.rate_limit import RateLimiter
from app.core.security import require_dev_admin
from app.modules.events.router import Ctx
from app.modules.ingest import service
from app.modules.ingest.schemas import ApproveResponse, UploadResponse

router = APIRouter(prefix="/events/{event_id}", tags=["upload (dev token)"])
Actor = Annotated[str, Depends(require_dev_admin)]
upload_limiter = RateLimiter("upload", limit=60, window_seconds=60)
MULTIPART_OVERHEAD = 64 * 1024


async def _read_files(
    request: Request, max_files: int, max_bytes: int
) -> tuple[list[bytes], UUID | None]:
    """
    Purpose: parse the multipart form with size limits.
    Outputs: (file contents, optional collection id). Raises PayloadTooLargeError or
        UnprocessableError. Side effects: reads the request body.
    """
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise PayloadTooLargeError("Content-Length is required")
    if int(length) > max_files * max_bytes + MULTIPART_OVERHEAD:
        raise PayloadTooLargeError("upload is larger than allowed")
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise UnprocessableError("upload must be multipart/form-data")
    try:
        form = await request.form(max_files=max_files, max_fields=5, max_part_size=1024)
    except HTTPException as exc:
        if "too many" in str(exc.detail).lower():
            raise PayloadTooLargeError(f"at most {max_files} files per upload") from exc
        raise
    try:
        collection_raw = form.get("collection_id")
        collection_id = UUID(collection_raw) if isinstance(collection_raw, str) else None
        files: list[bytes] = []
        for item in form.getlist("files"):
            if not isinstance(item, UploadFile):
                raise UnprocessableError("files must be file parts")
            data = await item.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise PayloadTooLargeError(f"file {len(files) + 1} is larger than the upload limit")
            files.append(data)
    except ValueError as exc:
        raise UnprocessableError("collection_id must be a UUID") from exc
    finally:
        await form.close()
    return files, collection_id


@router.post(
    "/photos",
    status_code=201,
    response_model=UploadResponse,
    dependencies=[Depends(upload_limiter)],
)
async def upload_photos(event_id: UUID, request: Request, actor: Actor, ctx: Ctx) -> UploadResponse:
    """Purpose: upload photos into quarantine (validated, random ids)."""
    settings = ctx.settings
    files, collection_id = await _read_files(
        request, settings.upload_max_files, settings.upload_max_bytes
    )
    result = service.upload_photos(ctx, event_id, files, collection_id, actor)
    return UploadResponse(collection_id=result.collection_id, photo_ids=result.photo_ids)


@router.post("/collections/{collection_id}/approve", response_model=ApproveResponse)
def approve_collection(
    event_id: UUID, collection_id: UUID, actor: Actor, ctx: Ctx
) -> ApproveResponse:
    """Purpose: approve a collection; the worker then indexes the event."""
    count = service.approve_collection(ctx, event_id, collection_id, actor)
    return ApproveResponse(collection_id=collection_id, approved_photos=count, status="indexing")
