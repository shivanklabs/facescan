"""index_event job: analyse an event's approved photos and make the event searchable.

Steps (each database step in the event's own RLS transaction):
1. check stored engine versions; if versions match and event is searchable, process only
   approved photos incrementally, otherwise trigger a full rebuild;
2. per photo: read the original, detect/score/embed faces in memory, write a web-size copy,
   deduplicate existing faces for that photo, and store faces and scene embeddings;
3. gate, calibrate and cluster all stored faces; store clusters and calibration encrypted; mark
   photos indexed and the event searchable.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select

from app.core.context import AppContext
from app.core.errors import NotFoundError
from app.db.event_scope import event_transaction
from app.features.activity_log.service import record
from app.features.events import repository as events_repo
from app.features.events.service import data_key
from app.features.photo_processing import repository
from app.features.photo_processing.models import Face
from app.features.photo_upload.models import Photo

logger = logging.getLogger("facescan.vision_pipeline")
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
    Purpose: (re)build one event's searchable face index incrementally.
    Inputs: ctx, event_id.
    Outputs: IndexSummary (photo count, face count, weak faces count, cluster count).
    Side effects: reads originals, writes web copies, saves faces and scene embeddings,
        rebuilds clusters and calibration, updates photo/event status, writes audit row.
        A replaced web copy is removed only after the new one is committed; if that removal
        fails the photo stays indexed and a warning is logged.
    """
    derivatives = ctx.settings.storage_bucket_derivatives
    with event_transaction(ctx.sessions, event_id) as session:
        event = events_repo.get_event(session, event_id)
        if event is None:
            raise NotFoundError("event not found")
        key, key_version = data_key(session, ctx.keys, event_id)
        stored_versions = repository.load_engine_versions(session, event_id, key)
        full_rebuild = bool(stored_versions and stored_versions != ctx.analyzer.engine_versions)

        photo_status_filter: tuple[str, ...]
        if full_rebuild:
            repository.delete_event_model(session, event_id)
            event.status = "indexing"
            photo_status_filter = ("approved", "indexed")
        else:
            if event.status != "searchable":
                event.status = "indexing"
            photo_status_filter = ("approved",)

        photos = [
            (p.id, p.bucket, p.object_key, p.web_object_key)
            for p in session.scalars(
                select(Photo)
                .where(Photo.event_id == event_id, Photo.status.in_(photo_status_filter))
                .order_by(Photo.created_at, Photo.id)
            )
        ]

    for photo_id, bucket, object_key, old_web_key in photos:
        new_web_key: str | None = None
        try:
            original = ctx.storage.get_object(bucket, event_id, object_key)
            faces = ctx.analyzer.analyse_photo(original)
            scene_embedding = ctx.analyzer.embed_scene(original)
            new_web_key = ctx.storage.put_object(
                derivatives,
                event_id,
                ctx.analyzer.web_copy(original, ctx.settings.web_copy_max_side),
                "image/jpeg",
            )
            del original

            with event_transaction(ctx.sessions, event_id) as session:
                session.execute(
                    delete(Face).where(Face.event_id == event_id, Face.photo_id == photo_id)
                )
                repository.add_faces(session, event_id, photo_id, faces, key, key_version)
                repository.save_scene_embedding(session, event_id, photo_id, scene_embedding, key)
                photo = session.get(Photo, photo_id)
                if photo is not None:
                    photo.web_object_key = new_web_key
                    photo.status = "indexed"
        except Exception as exc:
            logger.warning(
                "photo analysis failed",
                extra={"photo_id": str(photo_id), "error": type(exc).__name__},
            )
            if new_web_key is not None:
                ctx.storage.delete_object(derivatives, event_id, new_web_key)
            with event_transaction(ctx.sessions, event_id) as session:
                photo = session.get(Photo, photo_id)
                if photo is not None:
                    photo.status = "failed"
        else:
            # The new web copy is committed: a failed removal of the old one must not undo it.
            if old_web_key is not None and old_web_key != new_web_key:
                try:
                    ctx.storage.delete_object(derivatives, event_id, old_web_key)
                except Exception as exc:
                    logger.warning(
                        "old web copy not removed",
                        extra={"photo_id": str(photo_id), "error": type(exc).__name__},
                    )

    with event_transaction(ctx.sessions, event_id) as session:
        repository.delete_clusters_and_calibration(session, event_id)
        stored = repository.load_faces(session, event_id, key)
        built = ctx.analyzer.build_event(stored)
        repository.save_event_model(
            session, event_id, built, ctx.analyzer.engine_versions, key, key_version
        )
        event = events_repo.get_event(session, event_id)
        if event is not None:
            event.status = "searchable"
            event.indexed_at = datetime.now(UTC)
        total_photos = (
            session.scalar(
                select(func.count(Photo.id)).where(
                    Photo.event_id == event_id, Photo.status == "indexed"
                )
            )
            or 0
        )
        summary = IndexSummary(
            photos=total_photos,
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
