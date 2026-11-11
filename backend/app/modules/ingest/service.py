"""Photo upload into quarantine and collection approval.

Every file is validated before anything is stored: size limit, type by content (magic bytes
and decoder), pixel limit and a full decode. Accepted photos go to the quarantine bucket under
random object keys. Approving a collection moves its photos to the photos bucket and queues
the index_event job. Every action writes an audit row.
"""

import contextlib
import hashlib
import io
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image
from sqlalchemy import func, select

from app.core.auth import CurrentUser
from app.core.context import AppContext
from app.core.deps import require_role
from app.core.errors import ConflictError, NotFoundError, PayloadTooLargeError, UnprocessableError
from app.db.event_scope import event_transaction
from app.modules.audit.service import record
from app.modules.events import repository as events_repo
from app.modules.events.models import CameraOffset, Collection, EventSection
from app.modules.ingest.models import Photo, UploadSession
from app.ports.face_analyzer import ImageInfo, ImageRejectedError

INDEX_EVENT_JOB = "index_event"
CHUNK_SIZE = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Ids of the collection and the stored photos."""

    collection_id: UUID
    photo_ids: list[UUID]
    already_uploaded: list[int] = field(default_factory=list)
    message: str | None = None


def compute_phash(data: bytes) -> str:
    """
    Purpose: compute 64-bit perceptual hash (pHash) using 2D DCT via Pillow and NumPy.
    Inputs: image bytes (JPEG, PNG, WebP).
    Outputs: 16-character hex string representing 64-bit perceptual hash.
    Side effects: none.
    """
    with Image.open(io.BytesIO(data)) as img:
        small = img.convert("L").resize((32, 32), Image.Resampling.BILINEAR)
        pixels = np.asarray(small, dtype=np.float32)

    n = np.arange(32, dtype=np.float32)
    k = np.arange(32, dtype=np.float32).reshape(-1, 1)
    dct_mat = np.cos((np.pi / 32.0) * (n + 0.5) * k)

    dct = dct_mat @ pixels @ dct_mat.T
    dct_low = dct[:8, :8]
    med = float(np.median(dct_low.flatten()[1:]))
    bits = (dct_low > med).flatten()
    packed = np.packbits(bits).tobytes()
    return packed.hex()


def phash_distance(h1: str, h2: str) -> int:
    """
    Purpose: calculate Hamming distance between two 16-character hex pHashes.
    Inputs: h1, h2 (16 hex chars each).
    Outputs: int Hamming distance (0 to 64).
    Side effects: none.
    """
    return (int(h1, 16) ^ int(h2, 16)).bit_count()


def extract_exif_metadata(
    data: bytes,
) -> tuple[datetime | None, str | None, str | None, str | None]:
    """
    Purpose: extract capture time, camera make, camera model, and camera serial from image EXIF.
    Inputs: raw image bytes.
    Outputs: tuple of (taken_at, camera_make, camera_model, camera_serial).
    Side effects: none.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            exif = img.getexif()
            if not exif:
                return None, None, None, None
            make = exif.get(271)  # Make
            model = exif.get(272)  # Model

            exif_ifd = exif.get_ifd(0x8769)
            date_raw = exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)
            serial_raw = exif_ifd.get(42033) or exif.get(42033)

            if isinstance(date_raw, bytes):
                date_str = date_raw.decode("utf-8", "ignore")
            elif date_raw is not None:
                date_str = str(date_raw)
            else:
                date_str = None

            taken_at: datetime | None = None
            if date_str:
                with contextlib.suppress(Exception):
                    dt = datetime.strptime(date_str.strip()[:19], "%Y:%m:%d %H:%M:%S")
                    taken_at = dt.replace(tzinfo=UTC)

            camera_make = str(make).strip() if make is not None else None
            camera_model = str(model).strip() if model is not None else None
            camera_serial = str(serial_raw).strip() if serial_raw is not None else None
            return taken_at, camera_make, camera_model, camera_serial
    except Exception:
        return None, None, None, None


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
    ctx: AppContext,
    event_id: UUID,
    files: list[bytes],
    collection_id: UUID | None,
    user: CurrentUser,
) -> UploadResult:
    """
    Purpose: validate and store photos in quarantine for one event.
    Inputs: event, file contents, optional open collection to add to (else a new one), the
        signed-in photographer or organiser.
    Side effects: writes objects to the quarantine bucket; inserts collections/photos/audit
        rows. Objects are removed again if the database transaction fails.
    """
    infos = validate_files(ctx, files)
    bucket = ctx.settings.storage_bucket_quarantine
    stored: list[str] = []
    try:
        with event_transaction(ctx.sessions, event_id) as session:
            require_role(session, event_id, user.user_id, ("photographer", "organiser"))
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
            # Query existing SHA-256 digests and perceptual hashes in this event
            existing_sha256s = set(
                session.scalars(
                    select(Photo.sha256).where(
                        Photo.event_id == event_id,
                        Photo.sha256.is_not(None),
                    )
                ).all()
            )
            existing_phashes: list[tuple[UUID, str]] = [
                (row[0], str(row[1]))
                for row in session.execute(
                    select(Photo.id, Photo.phash).where(
                        Photo.event_id == event_id,
                        Photo.phash.is_not(None),
                    )
                ).all()
                if row[1] is not None
            ]

            # Query camera offsets and sections for this event
            camera_offsets = list(
                session.scalars(select(CameraOffset).where(CameraOffset.event_id == event_id))
            )
            sections = list(
                session.scalars(select(EventSection).where(EventSection.event_id == event_id))
            )

            photo_ids: list[UUID] = []
            already_uploaded: list[int] = []

            for idx, (data, info) in enumerate(zip(files, infos, strict=True), start=1):
                digest = hashlib.sha256(data).hexdigest()
                if digest in existing_sha256s:
                    already_uploaded.append(idx)
                    continue

                curr_phash = compute_phash(data)
                is_near_dup = any(
                    phash_distance(curr_phash, prev_hash) <= 10 for _, prev_hash in existing_phashes
                )

                taken_at, camera_make, camera_model, camera_serial = extract_exif_metadata(data)

                # Camera offset lookup
                offset_sec = 0
                if camera_model:
                    for off in camera_offsets:
                        if off.camera_model == camera_model and (
                            off.camera_serial is None or off.camera_serial == camera_serial
                        ):
                            offset_sec = off.offset_seconds
                            break

                if taken_at is not None:
                    corrected_time = taken_at + timedelta(seconds=offset_sec)
                else:
                    corrected_time = datetime.now(UTC)

                # Section assignment based on corrected_time
                assigned_section_id: UUID | None = None
                for s in sections:
                    if s.starts_at and s.ends_at and s.starts_at <= corrected_time <= s.ends_at:
                        assigned_section_id = s.id
                        break

                key = ctx.storage.put_object(bucket, event_id, data, info.content_type)
                stored.append(key)
                photo = Photo(
                    event_id=event_id,
                    collection_id=collection.id,
                    section_id=assigned_section_id,
                    bucket=bucket,
                    object_key=key,
                    content_type=info.content_type,
                    byte_size=len(data),
                    width=info.width,
                    height=info.height,
                    sha256=digest,
                    phash=curr_phash,
                    is_near_duplicate=is_near_dup,
                    taken_at=taken_at,
                    camera_make=camera_make,
                    camera_model=camera_model,
                    camera_serial=camera_serial,
                    corrected_time=corrected_time,
                )
                session.add(photo)
                session.flush()
                photo_ids.append(photo.id)
                existing_sha256s.add(digest)
                existing_phashes.append((photo.id, curr_phash))

            message = None
            if already_uploaded:
                if not photo_ids:
                    message = "already uploaded"
                else:
                    message = f"{len(already_uploaded)} file(s) already uploaded"

            record(
                session,
                event_id=event_id,
                actor=user.actor,
                action="photos.uploaded",
                target=str(collection.id),
                details={
                    "count": len(photo_ids),
                    "already_uploaded": len(already_uploaded),
                },
            )
            return UploadResult(
                collection_id=collection.id,
                photo_ids=photo_ids,
                already_uploaded=already_uploaded,
                message=message,
            )
    except Exception:
        for key in stored:
            ctx.storage.delete_object(bucket, event_id, key)
        raise


def approve_collection(
    ctx: AppContext, event_id: UUID, collection_id: UUID, user: CurrentUser
) -> int:
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
            require_role(session, event_id, user.user_id, ("organiser",))
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
            if event.status != "searchable":
                event.status = "indexing"
            ctx.queue.enqueue(session, event_id, INDEX_EVENT_JOB, {"reason": "collection_approved"})
            record(
                session,
                event_id=event_id,
                actor=user.actor,
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


@dataclass(frozen=True, slots=True)
class CollectionView:
    """A collection with its photo count and short-lived preview links (organiser only)."""

    collection_id: UUID
    status: str
    photos: int
    previews: list[str]


PREVIEWS_PER_COLLECTION = 12


def list_collections(ctx: AppContext, event_id: UUID, user: CurrentUser) -> list[CollectionView]:
    """
    Purpose: collections for the approval page, each with signed preview URLs of its photos
        (issued only after the organiser check; they expire after a few minutes).
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))
        result = []
        for collection in session.scalars(
            select(Collection)
            .where(Collection.event_id == event_id)
            .order_by(Collection.created_at.desc())
        ):
            photos = list(
                session.scalars(
                    select(Photo)
                    .where(Photo.collection_id == collection.id)
                    .order_by(Photo.created_at)
                )
            )
            previews = []
            for photo in photos[:PREVIEWS_PER_COLLECTION]:
                bucket, key = photo.bucket, photo.object_key
                if photo.web_object_key is not None:
                    bucket, key = ctx.settings.storage_bucket_derivatives, photo.web_object_key
                previews.append(ctx.storage.create_signed_download_url(bucket, event_id, key).url)
            result.append(CollectionView(collection.id, collection.status, len(photos), previews))
        return result


def init_upload_session(
    ctx: AppContext,
    event_id: UUID,
    collection_id: UUID | None,
    filename: str,
    byte_size: int,
    total_chunks: int,
    expected_sha256: str,
    user: CurrentUser,
) -> tuple[UploadSession, int]:
    """
    Purpose: start a resumable chunked upload session for an event collection.
    Inputs: event_id, collection_id, filename, byte_size, total_chunks, expected_sha256, user.
    Outputs: tuple of (UploadSession, chunk_size).
    Side effects: inserts upload_sessions row; creates chunk spool directory.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("photographer", "organiser"))
        if byte_size > ctx.settings.upload_max_bytes:
            raise PayloadTooLargeError("file size exceeds upload limit")
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
            coll = session.get(Collection, collection_id)
            if coll is None or coll.event_id != event_id:
                raise NotFoundError("collection not found")
            if coll.status != "open":
                raise ConflictError("collection is already approved")
            collection = coll

        upload_session = UploadSession(
            event_id=event_id,
            user_id=user.user_id,
            collection_id=collection.id,
            filename=filename,
            byte_size=byte_size,
            total_chunks=total_chunks,
            expected_sha256=expected_sha256.lower(),
            received_chunks=[],
            status="in_progress",
        )
        session.add(upload_session)
        session.flush()
        chunk_dir = Path(ctx.settings.upload_spool_dir) / "chunks" / str(upload_session.id)
        chunk_dir.mkdir(parents=True, exist_ok=True)
        return upload_session, CHUNK_SIZE


def upload_chunk(
    ctx: AppContext,
    event_id: UUID,
    upload_id: UUID,
    chunk_index: int,
    chunk_sha256: str | None,
    chunk_data: bytes,
    user: CurrentUser,
) -> list[int]:
    """
    Purpose: store one validated chunk of a chunked upload using row-level locking.
    Inputs: event_id, upload_id, chunk_index, chunk_sha256, chunk_data, user.
    Outputs: sorted list of received chunk indices.
    Side effects: writes chunk to disk spool; updates upload_sessions.received_chunks.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("photographer", "organiser"))
        upload_sess = session.scalar(
            select(UploadSession).where(UploadSession.id == upload_id).with_for_update()
        )
        if upload_sess is None or upload_sess.event_id != event_id:
            raise NotFoundError("upload session not found")
        if upload_sess.status != "in_progress":
            raise ConflictError(f"upload session is {upload_sess.status}")
        if chunk_index < 0 or chunk_index >= upload_sess.total_chunks:
            raise UnprocessableError(
                f"chunk index {chunk_index} out of range (0..{upload_sess.total_chunks - 1})"
            )

        computed_sha256 = hashlib.sha256(chunk_data).hexdigest()
        if chunk_sha256 and computed_sha256.lower() != chunk_sha256.lower():
            raise UnprocessableError("chunk sha256 checksum mismatch")

        chunk_dir = Path(ctx.settings.upload_spool_dir) / "chunks" / str(upload_id)
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_file = chunk_dir / f"{chunk_index}.chunk"
        chunk_file.write_bytes(chunk_data)

        received = set(upload_sess.received_chunks)
        received.add(chunk_index)
        upload_sess.received_chunks = sorted(received)
        session.flush()
        return list(upload_sess.received_chunks)


def get_upload_session(
    ctx: AppContext,
    event_id: UUID,
    upload_id: UUID,
    user: CurrentUser,
) -> UploadSession:
    """
    Purpose: inspect status and received chunks of a chunked upload session.
    Inputs: event_id, upload_id, user.
    Outputs: UploadSession.
    Side effects: none.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("photographer", "organiser"))
        upload_sess = session.get(UploadSession, upload_id)
        if upload_sess is None or upload_sess.event_id != event_id:
            raise NotFoundError("upload session not found")
        return upload_sess


def complete_upload_session(
    ctx: AppContext,
    event_id: UUID,
    upload_id: UUID,
    user: CurrentUser,
) -> UploadResult:
    """
    Purpose: assemble chunks, verify SHA-256, ingest photo, and clean spool files safely.
    Inputs: event_id, upload_id, user.
    Outputs: UploadResult.
    Side effects: stores photo in quarantine; deletes chunk files; updates session status.
    """
    chunk_dir = Path(ctx.settings.upload_spool_dir) / "chunks" / str(upload_id)
    buffer = bytearray()
    collection_id: UUID
    mismatch = False

    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("photographer", "organiser"))
        upload_sess = session.scalar(
            select(UploadSession).where(UploadSession.id == upload_id).with_for_update()
        )
        if upload_sess is None or upload_sess.event_id != event_id:
            raise NotFoundError("upload session not found")
        if upload_sess.status != "in_progress":
            raise ConflictError(f"upload session is {upload_sess.status}")
        if len(set(upload_sess.received_chunks)) != upload_sess.total_chunks:
            rec_count = len(upload_sess.received_chunks)
            raise UnprocessableError(
                f"incomplete upload: {rec_count}/{upload_sess.total_chunks} chunks received"
            )

        # Reassemble chunks in sequential order
        hasher = hashlib.sha256()
        for idx in range(upload_sess.total_chunks):
            chunk_file = chunk_dir / f"{idx}.chunk"
            if not chunk_file.exists():
                raise UnprocessableError(f"missing chunk file {idx}")
            chunk_bytes = chunk_file.read_bytes()
            hasher.update(chunk_bytes)
            buffer.extend(chunk_bytes)

        assembled_sha256 = hasher.hexdigest().lower()
        if assembled_sha256 != upload_sess.expected_sha256.lower():
            upload_sess.status = "failed"
            mismatch = True
        else:
            collection_id = upload_sess.collection_id

    if mismatch:
        raise UnprocessableError("assembled file SHA-256 mismatch")

    # Ingest the assembled file; if it raises, session remains in_progress and chunk_dir stays
    result = upload_photos(ctx, event_id, [bytes(buffer)], collection_id, user)

    # After upload_photos returns successfully, mark completed and remove chunk folder
    with event_transaction(ctx.sessions, event_id) as session:
        sess = session.get(UploadSession, upload_id)
        if sess is not None:
            sess.status = "completed"

    shutil.rmtree(chunk_dir, ignore_errors=True)
    return result


@dataclass(frozen=True, slots=True)
class ProcessingStatusView:
    quarantined: int
    approved: int
    indexed: int
    failed: int
    near_duplicates: int
    failed_photos: list[tuple[UUID, str, datetime]]


def get_processing_status(
    ctx: AppContext,
    event_id: UUID,
    user: CurrentUser,
) -> ProcessingStatusView:
    """
    Purpose: query photo status counts and failed photo list for photographer dashboard.
    Inputs: event_id, user.
    Outputs: ProcessingStatusView.
    Side effects: none.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("photographer", "organiser"))
        counts_by_status = dict(
            session.execute(
                select(Photo.status, func.count(Photo.id))
                .where(Photo.event_id == event_id)
                .group_by(Photo.status)
            ).all()
        )
        near_duplicates = (
            session.scalar(
                select(func.count(Photo.id)).where(
                    Photo.event_id == event_id, Photo.is_near_duplicate.is_(True)
                )
            )
            or 0
        )
        failed_photos = [
            (row[0], row[1], row[2])
            for row in session.execute(
                select(Photo.id, Photo.object_key, Photo.created_at)
                .where(Photo.event_id == event_id, Photo.status == "failed")
                .order_by(Photo.created_at.desc())
            ).all()
        ]
        return ProcessingStatusView(
            quarantined=counts_by_status.get("quarantined", 0),
            approved=counts_by_status.get("approved", 0),
            indexed=counts_by_status.get("indexed", 0),
            failed=counts_by_status.get("failed", 0),
            near_duplicates=near_duplicates,
            failed_photos=failed_photos,
        )


def retry_failed_photo(
    ctx: AppContext,
    event_id: UUID,
    photo_id: UUID,
    user: CurrentUser,
) -> str:
    """
    Purpose: reset a failed photo to approved state and queue index_event job.
    Inputs: event_id, photo_id, user.
    Outputs: new status string ("approved").
    Side effects: updates photo.status; enqueues index_event job; writes audit log.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("photographer", "organiser"))
        photo = session.get(Photo, photo_id)
        if photo is None or photo.event_id != event_id:
            raise NotFoundError("photo not found")
        if photo.status != "failed":
            return photo.status
        photo.status = "approved"
        ctx.queue.enqueue(
            session,
            event_id,
            INDEX_EVENT_JOB,
            {"reason": "photo_retry", "photo_id": str(photo_id)},
        )
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="photo.retried",
            target=str(photo_id),
        )
        return "approved"
