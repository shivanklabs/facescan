"""Photo upload into quarantine and collection approval.

Every file is validated before anything is stored: size limit, type by content (magic bytes
and decoder), pixel limit and a full decode. Accepted photos go to the quarantine bucket under
random object keys. Approving a collection moves its photos to the photos bucket and queues
the index_event job. Every action writes an audit row.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.context import AppContext
from app.core.errors import ConflictError, NotFoundError, PayloadTooLargeError, UnprocessableError
from app.db.event_scope import event_transaction
from app.modules.audit.service import record
from app.modules.events import repository as events_repo
from app.modules.events.models import Collection
from app.modules.ingest.models import Photo
from app.ports.face_analyzer import ImageInfo, ImageRejectedError

INDEX_EVENT_JOB = "index_event"


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Ids of the collection and the stored photos."""

    collection_id: UUID
    photo_ids: list[UUID]


def validate_files(ctx: AppContext, files: list[bytes]) -> list[ImageInfo]:
    """
    Purpose: check every file before storing any (all-or-nothing).
    Raises PayloadTooLargeError or UnprocessableError naming the file position only.
    """
    settings = ctx.settings
    if not files:
        raise UnprocessableError("no files uploaded")
    if len(files) > settings.upload_max_files:
        raise PayloadTooLargeError(f"at most {settings.upload_max_files} files per upload")
    infos = []
    for position, data in enumerate(files, start=1):
        if len(data) > settings.upload_max_bytes:
            raise PayloadTooLargeError(f"file {position} is larger than the upload limit")
        try:
            infos.append(ctx.analyzer.inspect_image(data, settings.upload_max_pixels))
        except ImageRejectedError as exc:
            raise UnprocessableError(f"file {position}: {exc}") from exc
    return infos


def upload_photos(
    ctx: AppContext, event_id: UUID, files: list[bytes], collection_id: UUID | None, actor: str
) -> UploadResult:
    """
    Purpose: validate and store photos in quarantine for one event.
    Inputs: event, file contents, optional open collection to add to (else a new one).
    Side effects: writes objects to the quarantine bucket; inserts collections/photos/audit
        rows. Objects are removed again if the database transaction fails.
    """
    infos = validate_files(ctx, files)
    bucket = ctx.settings.storage_bucket_quarantine
    stored: list[str] = []
    try:
        with event_transaction(ctx.sessions, event_id) as session:
            event = events_repo.get_event(session, event_id)
            if event is None:
                raise NotFoundError("event not found")
            if event.status == "closed":
                raise ConflictError("event is closed")
            if collection_id is None:
                collection = Collection(event_id=event_id)
                session.add(collection)
                session.flush()
            else:
                found = session.get(Collection, collection_id)
                if found is None or found.event_id != event_id:
                    raise NotFoundError("collection not found")
                if found.status != "open":
                    raise ConflictError("collection is already approved")
                collection = found
            photo_ids = []
            for data, info in zip(files, infos, strict=True):
                key = ctx.storage.put_object(bucket, event_id, data, info.content_type)
                stored.append(key)
                photo = Photo(
                    event_id=event_id,
                    collection_id=collection.id,
                    bucket=bucket,
                    object_key=key,
                    content_type=info.content_type,
                    byte_size=len(data),
                    width=info.width,
                    height=info.height,
                )
                session.add(photo)
                session.flush()
                photo_ids.append(photo.id)
            record(
                session,
                event_id=event_id,
                actor=actor,
                action="photos.uploaded",
                target=str(collection.id),
                details={"count": len(photo_ids)},
            )
            return UploadResult(collection.id, photo_ids)
    except Exception:
        for key in stored:
            ctx.storage.delete_object(bucket, event_id, key)
        raise


def approve_collection(ctx: AppContext, event_id: UUID, collection_id: UUID, actor: str) -> int:
    """
    Purpose: release a collection for indexing: move its photos out of quarantine and queue
        index_event. Outputs: number of photos approved.
    Side effects: copies objects to the photos bucket (originals removed after commit);
        updates photos/collections/events; inserts jobs and audit rows.
    """
    photos_bucket = ctx.settings.storage_bucket_photos
    moved: list[tuple[str, str]] = []  # (quarantine bucket, old key) to delete after commit
    new_keys: list[str] = []
    try:
        with event_transaction(ctx.sessions, event_id) as session:
            event = events_repo.get_event(session, event_id)
            collection = session.get(Collection, collection_id)
            if event is None or collection is None or collection.event_id != event_id:
                raise NotFoundError("collection not found")
            if collection.status != "open":
                raise ConflictError("collection is already approved")
            photos = list(
                session.scalars(
                    select(Photo).where(
                        Photo.collection_id == collection_id, Photo.status == "quarantined"
                    )
                )
            )
            for photo in photos:
                data = ctx.storage.get_object(photo.bucket, event_id, photo.object_key)
                key = ctx.storage.put_object(photos_bucket, event_id, data, photo.content_type)
                new_keys.append(key)
                moved.append((photo.bucket, photo.object_key))
                photo.bucket, photo.object_key, photo.status = photos_bucket, key, "approved"
            collection.status = "approved"
            collection.approved_at = datetime.now(UTC)
            event.status = "indexing"
            ctx.queue.enqueue(session, event_id, INDEX_EVENT_JOB, {"reason": "collection_approved"})
            record(
                session,
                event_id=event_id,
                actor=actor,
                action="collection.approved",
                target=str(collection_id),
                details={"photos": len(photos)},
            )
    except Exception:
        for key in new_keys:
            ctx.storage.delete_object(photos_bucket, event_id, key)
        raise
    for bucket, key in moved:
        ctx.storage.delete_object(bucket, event_id, key)
    return len(moved)
