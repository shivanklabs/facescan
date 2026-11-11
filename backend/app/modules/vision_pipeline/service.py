"""index_event job: analyse an event's approved photos and make the event searchable.

Steps (each database step in the event's own RLS transaction):
1. load the photo list and the data key; delete the previous faces/clusters/calibration;
2. per photo: read the original through the storage port, detect/score/embed faces in memory,
   write a web-size copy with EXIF stripped to the derivatives bucket, store the faces
   encrypted under the event key;
3. gate, calibrate and cluster all faces; store clusters and calibration encrypted; mark the
   photos indexed and the event searchable.
Re-running the job rebuilds the event from scratch (idempotent).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.context import AppContext
from app.core.errors import NotFoundError
from app.db.event_scope import event_transaction
from app.modules.audit.service import record
from app.modules.events import repository as events_repo
from app.modules.events.service import data_key
from app.modules.ingest.models import Photo
from app.modules.vision_pipeline import repository

WORKER_ACTOR = "worker"


@dataclass(frozen=True, slots=True)
class IndexSummary:
    """Counts of one index run (no personal data)."""

    photos: int
    faces: int
    weak_faces: int
    clusters: int


def index_event(ctx: AppContext, event_id: UUID) -> IndexSummary:
    """
    Purpose: (re)build one event's searchable face index.
    Side effects: reads originals, writes web copies, replaces faces/clusters/calibration,
        updates photos and the event, writes an audit row.
    """
    derivatives = ctx.settings.storage_bucket_derivatives
    with event_transaction(ctx.sessions, event_id) as session:
        event = events_repo.get_event(session, event_id)
        if event is None:
            raise NotFoundError("event not found")
        key, key_version = data_key(session, ctx.keys, event_id)
        photos = [
            (p.id, p.bucket, p.object_key, p.web_object_key)
            for p in session.scalars(
                select(Photo)
                .where(Photo.event_id == event_id, Photo.status.in_(("approved", "indexed")))
                .order_by(Photo.created_at, Photo.id)
            )
        ]
        repository.delete_event_model(session, event_id)
        event.status = "indexing"

    for photo_id, bucket, object_key, old_web_key in photos:
        original = ctx.storage.get_object(bucket, event_id, object_key)
        faces = ctx.analyzer.analyse_photo(original)
        scene_embedding = ctx.analyzer.embed_scene(original)
        web_key = ctx.storage.put_object(
            derivatives,
            event_id,
            ctx.analyzer.web_copy(original, ctx.settings.web_copy_max_side),
            "image/jpeg",
        )
        del original
        with event_transaction(ctx.sessions, event_id) as session:
            repository.add_faces(session, event_id, photo_id, faces, key, key_version)
            repository.save_scene_embedding(session, event_id, photo_id, scene_embedding, key)
            photo = session.get(Photo, photo_id)
            if photo is not None:
                photo.web_object_key = web_key
                photo.status = "indexed"
        if old_web_key is not None:
            ctx.storage.delete_object(derivatives, event_id, old_web_key)

    with event_transaction(ctx.sessions, event_id) as session:
        stored = repository.load_faces(session, event_id, key)
        built = ctx.analyzer.build_event(stored)
        repository.save_event_model(
            session, event_id, built, ctx.analyzer.engine_versions, key, key_version
        )
        event = events_repo.get_event(session, event_id)
        if event is not None:
            event.status = "searchable"
            event.indexed_at = datetime.now(UTC)
        summary = IndexSummary(
            photos=len(photos),
            faces=len(stored),
            weak_faces=sum(1 for w in built.weak.values() if w),
            clusters=len(built.clusters),
        )
        record(
            session,
            event_id=event_id,
            actor=WORKER_ACTOR,
            action="event.indexed",
            details={
                "photos": summary.photos,
                "faces": summary.faces,
                "weak_faces": summary.weak_faces,
                "clusters": summary.clusters,
            },
        )
    return summary


def mark_index_failed(ctx: AppContext, event_id: UUID) -> None:
    """Purpose: flag the event after the last failed attempt. Side effects: updates event."""
    with event_transaction(ctx.sessions, event_id) as session:
        event = events_repo.get_event(session, event_id)
        if event is not None:
            event.status = "failed"
            record(session, event_id=event_id, actor=WORKER_ACTOR, action="event.index_failed")
