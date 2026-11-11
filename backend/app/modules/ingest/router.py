"""HTTP routes for photo upload and collection approval.

Upload: photographers and organisers (passkey session). Approval: organisers. Uploads are multipart
(field name "files", optional "collection_id"); the request size is checked from
Content-Length before the body is parsed. Parts up to 1 MB stay in memory; larger event photos
are spooled to FACESCAN_UPLOAD_SPOOL_DIR (under var/, git-ignored) while parsing and deleted
when the request ends (app/core/multipart.py). Selfies never take this path.
"""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.core.deps import Ctx, PasskeyUser
from app.core.errors import PayloadTooLargeError, UnprocessableError
from app.core.multipart import MultiPartException, parse_upload_form
from app.core.rate_limit import RateLimiter
from app.modules.ingest import service
from app.modules.ingest.schemas import (
    ApproveResponse,
    CollectionListResponse,
    CollectionResponse,
    FailedPhotoInfo,
    ProcessingStatusResponse,
    RetryPhotoResponse,
    UploadChunkResponse,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadResponse,
    UploadStatusResponse,
)

router = APIRouter(prefix="/events/{event_id}", tags=["upload"])
upload_limiter = RateLimiter("upload", limit=60, window_seconds=60)
MULTIPART_OVERHEAD = 64 * 1024


async def _read_files(
    request: Request, max_files: int, max_bytes: int, spool_dir: Path
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
        form = await parse_upload_form(
            request.headers,
            request.stream(),
            spool_dir,
            max_files=max_files,
            max_fields=5,
            max_part_size=1024,
        )
    except MultiPartException as exc:
        if "too many files" in exc.message.lower():
            raise PayloadTooLargeError(f"at most {max_files} files per upload") from exc
        raise UnprocessableError("malformed multipart upload") from exc
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
async def upload_photos(
    event_id: UUID, request: Request, user: PasskeyUser, ctx: Ctx
) -> UploadResponse:
    """
    Purpose: upload photos into quarantine offloaded to a threadpool.
    Inputs: event_id, request, user (PasskeyUser), app context.
    Outputs: UploadResponse with collection_id, photo_ids, and messages.
    Side effects: saves uploaded photos to quarantine storage and DB.
    """
    settings = ctx.settings
    files, collection_id = await _read_files(
        request,
        settings.upload_max_files,
        settings.upload_max_bytes,
        Path(settings.upload_spool_dir),
    )
    result = await run_in_threadpool(
        service.upload_photos, ctx, event_id, files, collection_id, user
    )
    return UploadResponse(
        collection_id=result.collection_id,
        photo_ids=result.photo_ids,
        already_uploaded=list(result.already_uploaded),
        message=result.message,
    )


@router.post("/collections/{collection_id}/approve", response_model=ApproveResponse)
def approve_collection(
    event_id: UUID, collection_id: UUID, user: PasskeyUser, ctx: Ctx
) -> ApproveResponse:
    """Purpose: approve a collection; the worker then indexes the event."""
    count = service.approve_collection(ctx, event_id, collection_id, user)
    return ApproveResponse(collection_id=collection_id, approved_photos=count, status="indexing")


@router.get("/collections", response_model=CollectionListResponse)
def list_collections(event_id: UUID, user: PasskeyUser, ctx: Ctx) -> CollectionListResponse:
    """Purpose: collections with counts and signed previews (organiser)."""
    return CollectionListResponse(
        collections=[
            CollectionResponse(
                collection_id=c.collection_id, status=c.status, photos=c.photos, previews=c.previews
            )
            for c in service.list_collections(ctx, event_id, user)
        ]
    )


@router.post("/uploads/init", status_code=201, response_model=UploadInitResponse)
def init_chunked_upload(
    event_id: UUID, body: UploadInitRequest, user: PasskeyUser, ctx: Ctx
) -> UploadInitResponse:
    """Purpose: initialize a resumable chunked upload session."""
    session, chunk_size = service.init_upload_session(
        ctx,
        event_id,
        body.collection_id,
        body.filename,
        body.byte_size,
        body.total_chunks,
        body.expected_sha256,
        user,
    )
    return UploadInitResponse(
        upload_id=session.id,
        collection_id=session.collection_id,
        chunk_size=chunk_size,
        total_chunks=session.total_chunks,
        received_chunks=list(session.received_chunks),
    )


@router.post(
    "/uploads/{upload_id}/chunk",
    response_model=UploadChunkResponse,
    dependencies=[Depends(upload_limiter)],
)
async def upload_chunk_part(
    event_id: UUID,
    upload_id: UUID,
    request: Request,
    user: PasskeyUser,
    ctx: Ctx,
    chunk_index: int = Query(..., ge=0),
    chunk_sha256: str | None = Query(default=None),
) -> UploadChunkResponse:
    """
    Purpose: receive and verify a single chunk in a chunked upload offloaded to threadpool.
    Inputs: event_id, upload_id, request, user, ctx, chunk_index, chunk_sha256.
    Outputs: UploadChunkResponse with received_chunks and complete flag.
    Side effects: writes chunk to storage and updates upload session in database.
    """
    max_chunk = service.CHUNK_SIZE
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > max_chunk:
        raise PayloadTooLargeError("chunk exceeds maximum chunk size")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_chunk:
            raise PayloadTooLargeError("chunk exceeds maximum chunk size")
        chunks.append(chunk)

    chunk_data = b"".join(chunks)
    if not chunk_data:
        raise UnprocessableError("empty chunk data")
    received = await run_in_threadpool(
        service.upload_chunk,
        ctx,
        event_id,
        upload_id,
        chunk_index,
        chunk_sha256,
        chunk_data,
        user,
    )
    upload_sess = await run_in_threadpool(
        service.get_upload_session, ctx, event_id, upload_id, user
    )
    return UploadChunkResponse(
        received_chunks=received,
        complete=len(received) == upload_sess.total_chunks,
    )


@router.get("/uploads/{upload_id}", response_model=UploadStatusResponse)
def get_chunked_upload_status(
    event_id: UUID, upload_id: UUID, user: PasskeyUser, ctx: Ctx
) -> UploadStatusResponse:
    """Purpose: inspect state of a chunked upload for resuming."""
    sess = service.get_upload_session(ctx, event_id, upload_id, user)
    return UploadStatusResponse(
        upload_id=sess.id,
        filename=sess.filename,
        byte_size=sess.byte_size,
        total_chunks=sess.total_chunks,
        received_chunks=list(sess.received_chunks),
        status=sess.status,
    )


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=UploadCompleteResponse,
    dependencies=[Depends(upload_limiter)],
)
def complete_chunked_upload(
    event_id: UUID, upload_id: UUID, user: PasskeyUser, ctx: Ctx
) -> UploadCompleteResponse:
    """
    Purpose: finalize chunked upload, verify full SHA-256 and ingest photo.
    Inputs: event_id, upload_id, user, ctx.
    Outputs: UploadCompleteResponse.
    Side effects: assembles photo in quarantine; removes chunk files on success.
    """
    result = service.complete_upload_session(ctx, event_id, upload_id, user)
    return UploadCompleteResponse(
        collection_id=result.collection_id,
        photo_id=result.photo_ids[0] if result.photo_ids else None,
        status="approved" if result.photo_ids else "already_uploaded",
        already_uploaded=bool(result.already_uploaded and not result.photo_ids),
        message=result.message,
    )


@router.get("/processing-status", response_model=ProcessingStatusResponse)
def get_processing_status(event_id: UUID, user: PasskeyUser, ctx: Ctx) -> ProcessingStatusResponse:
    """Purpose: photographer processing status counts and failed photos."""
    view = service.get_processing_status(ctx, event_id, user)
    return ProcessingStatusResponse(
        quarantined=view.quarantined,
        approved=view.approved,
        indexed=view.indexed,
        failed=view.failed,
        near_duplicates=view.near_duplicates,
        failed_photos=[
            FailedPhotoInfo(id=pid, filename=fname, created_at=fcreated)
            for pid, fname, fcreated in view.failed_photos
        ],
    )


@router.post("/photos/{photo_id}/retry", response_model=RetryPhotoResponse)
def retry_photo(event_id: UUID, photo_id: UUID, user: PasskeyUser, ctx: Ctx) -> RetryPhotoResponse:
    """Purpose: retry a failed photo by setting it to approved and queueing index_event."""
    status = service.retry_failed_photo(ctx, event_id, photo_id, user)
    return RetryPhotoResponse(
        photo_id=photo_id,
        status=status,
        message="Photo queued for retry",
    )
