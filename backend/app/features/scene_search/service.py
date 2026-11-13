"""Service operations for scene search, blurred previews, and 1:1 claims.

Invariants:
- Row-level security: all queries scoped by event_id.
- Per-event encryption: scene embeddings and faces decrypted in memory using the event data key.
- Previews are server-blurred with face detection and 25% enlargement at low resolution.
- 1:1 claim check enforces liveness, quality gate, calibrated AuraFace threshold,
  and SFace second opinion.
- At most one pending claim per guest per face.
- PostgreSQL advisory lock per (event_id, user_id, kind) ensures atomic enforcement
  of daily limits under concurrent requests.
- All temporary selfie frames and embeddings stay in memory only and are never logged or stored.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np
from app.core.country_rules import load_country_policy as load_country_policy
from app.core.encryption import aad, open_sealed
from app.core.errors import (
    DomainError,
    NotFoundError,
    PayloadTooLargeError,
)
from app.core.login_sessions import CurrentUser
from app.core.request_checks import require_role
from app.core.services_setup import AppContext, ContextConfigError
from app.core.settings import Settings
from app.db.event_isolation import ADMIN_SCOPE, event_transaction, scoped_transaction
from app.features.activity_log.service import record
from app.features.consent.service import (
    check_guest_search_access,
)
from app.features.events.service import data_key
from app.features.face_matching.models import CannotLink, Match
from app.features.face_matching.service import embed_frames
from app.features.gallery.models import Claim
from app.features.photo_processing import repository as vision_repo
from app.features.photo_processing.models import Face
from app.features.photo_upload.models import Photo
from app.features.scene_search.models import (
    ClaimAttempt,
    SceneSearchAttempt,
    SceneSearchCandidate,
)
from app.features.scene_search.schemas import (
    MAX_FRAME_B64_CHARS,
    CandidateFace,
    FaceBBox,
    OrganiserClaimItem,
    OrganiserClaimQueueResponse,
    ReviewClaimRequest,
    ReviewClaimResponse,
    SceneCandidateItem,
    SceneSearchRequest,
    SceneSearchResponse,
    SubmitClaimRequest,
    SubmitClaimResponse,
)
from app.interfaces.photo_storage import ObjectNotFoundError
from fastapi import HTTPException
from sqlalchemy import func, select, text
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("facescan.scene_search")


def _advisory_lock_key(event_id: UUID, user_id: UUID, kind: str) -> int:
    """
    Purpose: compute a deterministic 64-bit signed integer for PostgreSQL pg_advisory_xact_lock.
    Inputs: event_id, user_id, kind ("scene_search" or "claim").
    Outputs: signed 64-bit int.
    Side effects: none.
    """
    digest = hashlib.sha256(f"{event_id}:{user_id}:{kind}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _signing_key_bytes(settings: Settings) -> bytes:
    """
    Purpose: get secret bytes for HMAC signing from storage_signing_key or master key.
    Inputs: settings.
    Outputs: key bytes.
    Side effects: raises ContextConfigError when neither key is configured (no built-in key).
    """
    if settings.storage_signing_key is not None:
        return settings.storage_signing_key.get_secret_value().encode("utf-8")
    if settings.keys_master_key is not None:
        return settings.keys_master_key.get_secret_value().encode("utf-8")
    raise ContextConfigError(
        "set FACESCAN_STORAGE_SIGNING_KEY or FACESCAN_KEYS_MASTER_KEY to sign preview links"
    )


def sign_preview_url(
    settings: Settings, event_id: UUID, photo_id: UUID, expiry_seconds: int = 3600
) -> str:
    """
    Purpose: generate short-lived HMAC signed preview URL for candidate photo.
    Inputs: settings, event_id, photo_id, optional expiry duration in seconds.
    Outputs: signed URL path string with query parameters.
    Side effects: none.
    """
    expires = int(time.time()) + expiry_seconds
    key = _signing_key_bytes(settings)
    message = f"/events/{event_id}/scene-search/photos/{photo_id}/preview:{expires}".encode()
    sig = hmac.new(key, message, hashlib.sha256).hexdigest()
    path = f"/events/{event_id}/scene-search/photos/{photo_id}/preview"
    return f"{path}?expires={expires}&signature={sig}"


def verify_preview_signature(
    settings: Settings, event_id: UUID, photo_id: UUID, expires: int, signature: str
) -> bool:
    """
    Purpose: verify that a preview URL signature is valid and has not expired.
    Inputs: settings, event_id, photo_id, expires timestamp, signature hex string.
    Outputs: bool.
    Side effects: none.
    """
    if expires < int(time.time()):
        return False
    key = _signing_key_bytes(settings)
    message = f"/events/{event_id}/scene-search/photos/{photo_id}/preview:{expires}".encode()
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _prepare_scene_search(
    ctx: AppContext, user: CurrentUser, event_id: UUID, req: SceneSearchRequest
) -> tuple[set[UUID], dict[UUID, np.ndarray]]:
    """
    Purpose: verify guest access, enforce daily rate limits with advisory lock,
        and load eligible photo IDs with scene embeddings.
    Inputs: ctx, user, event_id, req.
    Outputs: tuple of (eligible_photo_ids, scene_embeddings).
    Side effects: adds SceneSearchAttempt in database.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        check_guest_search_access(session, ctx, event_id, user)

        # Acquire per-guest transaction advisory lock to serialize concurrent attempts
        lock_key = _advisory_lock_key(event_id, user.user_id, "scene_search")
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        attempts = (
            session.scalar(
                select(func.count(SceneSearchAttempt.id)).where(
                    SceneSearchAttempt.event_id == event_id,
                    SceneSearchAttempt.user_id == user.user_id,
                    SceneSearchAttempt.created_at >= cutoff,
                )
            )
            or 0
        )
        if attempts >= ctx.settings.scene_search_per_guest_per_day:
            raise HTTPException(status_code=429, detail="scene_search_daily_limit_exceeded")

        session.add(SceneSearchAttempt(event_id=event_id, user_id=user.user_id))

        key, _ = data_key(session, ctx.keys, event_id)
        scene_embeddings = vision_repo.load_scene_embeddings(session, event_id, key)

        # Photos this guest has already received (never recommend already released photos)
        already_matched = set(
            session.scalars(
                select(Match.photo_id).where(
                    Match.event_id == event_id,
                    Match.user_id == user.user_id,
                    Match.score_band == "release",
                )
            )
        )

        # Filter photos by section and taken_at timestamps if requested
        stmt = select(Photo).where(
            Photo.event_id == event_id,
            Photo.status.in_(("approved", "indexed")),
            Photo.id.not_in(already_matched),
        )
        if req.section_id is not None:
            stmt = stmt.where(Photo.section_id == req.section_id)
        photo_time = func.coalesce(Photo.corrected_time, Photo.taken_at, Photo.created_at)
        if req.time_from is not None:
            stmt = stmt.where(photo_time >= req.time_from)
        if req.time_to is not None:
            stmt = stmt.where(photo_time <= req.time_to)

        eligible_photo_ids = set(session.scalars(stmt.with_only_columns(Photo.id)))

    return eligible_photo_ids, scene_embeddings


def _rank_and_save_candidates(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    phrases: list[str],
    is_rewritten: bool,
    eligible_photo_ids: set[UUID],
    scene_embeddings: dict[UUID, np.ndarray],
) -> SceneSearchResponse:
    """
    Purpose: compute scene query embeddings, rank candidate photos by cosine similarity,
        load faces, record candidate history, and build response.
    Inputs: ctx, user, event_id, phrases, is_rewritten, eligible_photo_ids, scene_embeddings.
    Outputs: SceneSearchResponse.
    Side effects: inserts SceneSearchCandidate rows and records audit log.
    """
    # 3. Compute normalized query embedding
    phrase_vecs = [ctx.analyzer.embed_scene_query(p) for p in phrases]
    query_vec = np.mean(phrase_vecs, axis=0)
    norm = max(float(np.linalg.norm(query_vec)), 1e-12)
    query_vec = (query_vec / norm).astype(np.float32)

    # 4. Dot product ranking over eligible photos in memory
    scores: list[tuple[float, UUID]] = []
    for pid, emb in scene_embeddings.items():
        if pid in eligible_photo_ids:
            score = float(np.dot(query_vec, emb))
            scores.append((score, pid))

    scores.sort(key=lambda x: x[0], reverse=True)
    top_candidates = [pid for _, pid in scores[:12]]

    # 5. Load faces for top photos, record server-side candidate history, and build response
    with event_transaction(ctx.sessions, event_id) as session:
        cannot_links = session.scalars(
            select(CannotLink).where(
                CannotLink.event_id == event_id,
                CannotLink.user_id == user.user_id,
            )
        ).all()
        cannot_photo_ids = {cl.photo_id for cl in cannot_links}
        cannot_face_ids = {cl.face_id for cl in cannot_links if cl.face_id is not None}

        filtered_candidates = [pid for pid in top_candidates if pid not in cannot_photo_ids]

        faces_by_photo: dict[UUID, list[CandidateFace]] = {pid: [] for pid in filtered_candidates}
        all_faces = session.scalars(
            select(Face).where(Face.event_id == event_id, Face.photo_id.in_(filtered_candidates))
        ).all()

        for f in all_faces:
            if f.id in cannot_face_ids:
                continue
            faces_by_photo[f.photo_id].append(
                CandidateFace(
                    face_id=f.id,
                    bbox=(f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h),
                )
            )
            # Record candidate face shown to this guest for subsequent 1:1 claim verification
            session.add(
                SceneSearchCandidate(
                    event_id=event_id,
                    user_id=user.user_id,
                    photo_id=f.photo_id,
                    face_id=f.id,
                )
            )

        # One query for the original sizes of all candidate photos (face boxes are in pixels).
        sizes = {
            row.id: (row.width, row.height)
            for row in session.execute(
                select(Photo.id, Photo.width, Photo.height).where(
                    Photo.event_id == event_id, Photo.id.in_(filtered_candidates)
                )
            )
        }
        items: list[SceneCandidateItem] = []
        for pid in filtered_candidates:
            if not faces_by_photo[pid] or pid not in sizes:
                continue
            preview_url = sign_preview_url(ctx.settings, event_id, pid)
            width, height = sizes[pid]
            items.append(
                SceneCandidateItem(
                    photo_id=pid,
                    preview_url=preview_url,
                    faces=faces_by_photo[pid],
                    photo_width=width,
                    photo_height=height,
                )
            )

        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="scene_search.performed",
            details={"candidates_count": len(items), "query_rewritten": is_rewritten},
        )

        return SceneSearchResponse(candidates=items, query_rewritten=is_rewritten)


async def search_scene(
    ctx: AppContext, user: CurrentUser, event_id: UUID, req: SceneSearchRequest
) -> SceneSearchResponse:
    """
    Purpose: execute natural language scene search over photos using SigLIP 2
        embeddings offloaded to threadpool.
    Inputs: ctx, user, event_id, request payload.
    Outputs: SceneSearchResponse with candidates, preview URLs, and face bounding boxes.
    Side effects: records scene_search_attempts, saves candidate faces, logs audit row.
    """
    eligible_photo_ids, scene_embeddings = await run_in_threadpool(
        _prepare_scene_search, ctx, user, event_id, req
    )
    if not eligible_photo_ids or not scene_embeddings:
        return SceneSearchResponse(candidates=[], query_rewritten=False)

    # 2. Query rewriting (Gemini) - falls back to local query on any failure
    rewritten_phrases: list[str] = []
    if ctx.rewriter is not None:
        rewritten_phrases = await ctx.rewriter.rewrite_query(req.query)
    is_rewritten = bool(rewritten_phrases and rewritten_phrases != [req.query])
    phrases = rewritten_phrases if rewritten_phrases else [req.query]

    return await run_in_threadpool(
        _rank_and_save_candidates,
        ctx,
        user,
        event_id,
        phrases,
        is_rewritten,
        eligible_photo_ids,
        scene_embeddings,
    )


def get_candidate_preview(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    photo_id: UUID,
    expires: int,
    signature: str,
) -> bytes:
    """
    Purpose: generate low-resolution blurred preview of candidate photo with all faces blurred.
    Inputs: ctx, user, event_id, photo_id, expires timestamp, signature.
    Outputs: JPEG bytes (max 480px, quality 85).
    Side effects: none.
    """
    if not verify_preview_signature(ctx.settings, event_id, photo_id, expires, signature):
        raise HTTPException(status_code=403, detail="invalid_or_expired_signature")

    with event_transaction(ctx.sessions, event_id) as session:
        check_guest_search_access(session, ctx, event_id, user)

        # Server-side verification: photo must be one of recent candidate photos
        cand = session.scalar(
            select(SceneSearchCandidate)
            .where(
                SceneSearchCandidate.event_id == event_id,
                SceneSearchCandidate.user_id == user.user_id,
                SceneSearchCandidate.photo_id == photo_id,
            )
            .limit(1)
        )
        if cand is None:
            raise HTTPException(status_code=403, detail="candidate_not_found_or_expired")

        photo = session.get(Photo, photo_id)
        if photo is None:
            raise NotFoundError("photo not found")
        bucket, object_key = photo.bucket, photo.object_key
        web_key = photo.web_object_key

    image_bytes: bytes | None = None
    if web_key is not None:
        try:
            image_bytes = ctx.storage.get_object(
                ctx.settings.storage_bucket_derivatives, event_id, web_key
            )
        except ObjectNotFoundError:
            image_bytes = None
    if image_bytes is None:
        image_bytes = ctx.storage.get_object(bucket, event_id, object_key)

    return ctx.analyzer.blurred_preview(
        image_bytes,
        max_side=ctx.settings.preview_max_side,
        jpeg_quality=ctx.settings.preview_jpeg_quality,
    )


def submit_claim(
    ctx: AppContext, user: CurrentUser, event_id: UUID, req: SubmitClaimRequest
) -> SubmitClaimResponse:
    """
    Purpose: submit a 1:1 face claim on a candidate face using fresh guided selfie scan.
    Inputs: ctx, user, event_id, SubmitClaimRequest.
    Outputs: SubmitClaimResponse (status: "released" or "queued_for_review").
    Side effects: inserts Match or Claim row, records ClaimAttempt, logs audit event.
    """
    # 1. Authorization, age, consent, search window, advisory lock and daily limit
    # must all happen before frame decoding, liveness or embeddings (same order as the face scan).
    with event_transaction(ctx.sessions, event_id) as session:
        check_guest_search_access(session, ctx, event_id, user)

        # Acquire transaction advisory lock for atomic claim limit enforcement
        lock_key = _advisory_lock_key(event_id, user.user_id, "claim")
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        attempts = (
            session.scalar(
                select(func.count(ClaimAttempt.id)).where(
                    ClaimAttempt.event_id == event_id,
                    ClaimAttempt.user_id == user.user_id,
                    ClaimAttempt.created_at >= cutoff,
                )
            )
            or 0
        )
        if attempts >= ctx.settings.claims_per_guest_per_day:
            raise HTTPException(status_code=429, detail="claim_daily_limit_exceeded")

        session.add(ClaimAttempt(event_id=event_id, user_id=user.user_id))

    # 2. Decode selfie frames in memory with size caps
    decoded_frames: list[bytes] = []
    for f_b64 in req.frames:
        if len(f_b64) > MAX_FRAME_B64_CHARS:
            raise PayloadTooLargeError("selfie frame exceeds maximum allowed size")
        try:
            raw = base64.b64decode(f_b64)
            if len(raw) > ctx.settings.selfie_max_bytes:
                raise PayloadTooLargeError("selfie frame exceeds maximum allowed size")
            decoded_frames.append(raw)
        except DomainError:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_base64_frame") from exc

    # 3. Liveness verification
    if ctx.liveness is not None:
        verdict = ctx.liveness.verify_liveness(decoded_frames, front_only=req.front_only)
        if not verdict.passed:
            raise HTTPException(status_code=422, detail="liveness_check_failed")
    elif ctx.settings.env != "dev":
        raise HTTPException(status_code=403, detail="liveness_required")

    # 4. Embed selfie frames in memory
    try:
        selfie_queries = embed_frames(ctx, list(enumerate(decoded_frames, start=1)))
    finally:
        del decoded_frames  # Invariant: raw frames discarded immediately from memory

    with event_transaction(ctx.sessions, event_id) as session:
        # Invariant: face must belong to photo and event
        face = session.get(Face, req.face_id)
        if face is None or face.photo_id != req.photo_id or face.event_id != event_id:
            raise HTTPException(status_code=400, detail="invalid_candidate_face")

        # Invariant: cannot claim a photo or face previously marked as 'not me'
        is_cannot = session.scalar(
            select(func.count(CannotLink.id)).where(
                CannotLink.event_id == event_id,
                CannotLink.user_id == user.user_id,
                (CannotLink.photo_id == req.photo_id) | (CannotLink.face_id == req.face_id),
            )
        )
        if is_cannot:
            raise HTTPException(status_code=400, detail="cannot_claim_marked_not_me")

        # 3. Invariant: candidate must have been presented to this guest in scene search
        cand = session.scalar(
            select(SceneSearchCandidate)
            .where(
                SceneSearchCandidate.event_id == event_id,
                SceneSearchCandidate.user_id == user.user_id,
                SceneSearchCandidate.photo_id == req.photo_id,
                SceneSearchCandidate.face_id == req.face_id,
            )
            .limit(1)
        )
        if cand is None:
            raise HTTPException(status_code=400, detail="candidate_not_found_or_expired")

        # 4. Invariant: at most one pending claim per guest per face
        pending = session.scalar(
            select(Claim)
            .where(
                Claim.event_id == event_id,
                Claim.user_id == user.user_id,
                Claim.face_id == req.face_id,
                Claim.status == "pending",
            )
            .limit(1)
        )
        if pending is not None:
            raise HTTPException(status_code=409, detail="claim_already_pending")

        # 5. Decrypt candidate face embeddings and load calibration
        key, _ = data_key(session, ctx.keys, event_id)
        associated = aad(event_id, "faces", face.id)
        face_payload = json.loads(open_sealed(key, face.embedding_encrypted, associated))
        cand_embeddings = vision_repo.unpack_vectors(face_payload["embeddings"])

        calibration = vision_repo.load_event_calibration(session, event_id, key)

        # 6. Perform 1:1 match applying all automatic release rules
        offset = ctx.settings.search_front_only_threshold_offset if req.front_only else 0.0
        passed = ctx.analyzer.match_face_1to1(
            candidate_embeddings=cand_embeddings,
            calibration=calibration,
            selfie_queries=selfie_queries,
            front_only=req.front_only,
            threshold_offset=offset,
        )

        if passed:
            # Automatic release to gallery with source = "claim"
            existing_match = session.scalar(
                select(Match)
                .where(
                    Match.event_id == event_id,
                    Match.user_id == user.user_id,
                    Match.photo_id == req.photo_id,
                )
                .limit(1)
            )
            if existing_match is None:
                session.add(
                    Match(
                        event_id=event_id,
                        user_id=user.user_id,
                        photo_id=req.photo_id,
                        score_band="release",
                        source="claim",
                        released_at=datetime.now(UTC),
                    )
                )
            else:
                if existing_match.score_band != "not_me":
                    existing_match.score_band = "release"
                    existing_match.released_at = datetime.now(UTC)
                existing_match.source = "claim"
            record(
                session,
                event_id=event_id,
                actor=user.actor,
                action="claim.matched",
                target=str(req.photo_id),
            )
            return SubmitClaimResponse(status="released", photo_id=req.photo_id)

        # Failed 1:1 check: photo stays locked, claim queued for organiser review
        reason = "front_only_scan" if req.front_only else "face_check_failed"
        session.add(
            Claim(
                event_id=event_id,
                user_id=user.user_id,
                photo_id=req.photo_id,
                face_id=req.face_id,
                status="pending",
                reason=reason,
            )
        )
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="claim.queued",
            target=str(req.photo_id),
        )
        return SubmitClaimResponse(status="queued_for_review", photo_id=req.photo_id)


def list_event_claims(
    ctx: AppContext, user: CurrentUser, event_id: UUID
) -> OrganiserClaimQueueResponse:
    """
    Purpose: list all pending claims for an event for the organiser.
    Inputs: ctx, user (must be organiser), event_id.
    Outputs: OrganiserClaimQueueResponse with photos, bounding boxes, a per-event label
        ("Guest #1", "Guest #2", ...) and reason. Organisers never see a guest's email, so emails
        are not read at all.
    Side effects: none (one joined query for claims, photos and faces).
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))

        rows = session.execute(
            select(Claim, Photo.web_object_key, Photo.width, Photo.height, Face)
            .outerjoin(Photo, Claim.photo_id == Photo.id)
            .outerjoin(Face, Claim.face_id == Face.id)
            .where(Claim.event_id == event_id, Claim.status == "pending")
            .order_by(Claim.created_at.asc())
        ).all()

        bucket = ctx.settings.storage_bucket_derivatives
        items: list[OrganiserClaimItem] = []
        guest_numbers: dict[UUID, int] = {}

        for claim, web_object_key, photo_width, photo_height, face in rows:
            if claim.user_id not in guest_numbers:
                guest_numbers[claim.user_id] = len(guest_numbers) + 1
            claimant_email = f"Guest #{guest_numbers[claim.user_id]}"

            if web_object_key:
                signed = ctx.storage.create_signed_download_url(bucket, event_id, web_object_key)
                photo_url = signed.url
            else:
                photo_url = ""

            bbox_model: FaceBBox | None = None
            if face is not None:
                bbox_model = FaceBBox(
                    x=face.bbox_x,
                    y=face.bbox_y,
                    width=face.bbox_w,
                    height=face.bbox_h,
                )

            items.append(
                OrganiserClaimItem(
                    claim_id=claim.id,
                    photo_id=claim.photo_id,
                    photo_url=photo_url,
                    face_id=claim.face_id,
                    face_bbox=bbox_model,
                    photo_width=photo_width,
                    photo_height=photo_height,
                    claimant_email=claimant_email,
                    reason=claim.reason,
                    status=claim.status,
                    created_at=claim.created_at,
                )
            )

        return OrganiserClaimQueueResponse(claims=items)


def review_claim(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    claim_id: UUID,
    req: ReviewClaimRequest,
) -> ReviewClaimResponse:
    """
    Purpose: atomically approve or reject a pending claim on behalf of the event organiser.
    Inputs: ctx, user (must be organiser), event_id, claim_id, req (action: "approve" | "reject").
    Outputs: ReviewClaimResponse.
    Side effects: updates Claim status and reviewed_at/by, creates Match on approve,
                  and logs audit event. Approving a photo the guest marked 'not me' raises
                  409 and changes nothing.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("organiser",))

        claim = session.scalar(
            select(Claim).where(Claim.id == claim_id, Claim.event_id == event_id).with_for_update()
        )
        if claim is None:
            raise NotFoundError("claim_not_found")

        if claim.status != "pending":
            raise HTTPException(status_code=409, detail="claim_not_pending")

        now = datetime.now(UTC)
        claim.reviewed_at = now
        claim.reviewed_by = user.user_id

        if req.action == "approve":
            claim.status = "approved"

            existing_match = session.scalar(
                select(Match)
                .where(
                    Match.event_id == event_id,
                    Match.photo_id == claim.photo_id,
                    Match.user_id == claim.user_id,
                )
                .limit(1)
            )
            if existing_match is None:
                session.add(
                    Match(
                        event_id=event_id,
                        user_id=claim.user_id,
                        photo_id=claim.photo_id,
                        score_band="release",
                        source="claim",
                        released_at=now,
                    )
                )
            elif existing_match.score_band == "not_me":
                # The guest said "not me": never flip it back. The rollback keeps the claim pending.
                raise HTTPException(status_code=409, detail="photo_marked_not_me")
            else:
                existing_match.score_band = "release"
                existing_match.released_at = now
                existing_match.source = "claim"

            record(
                session,
                event_id=event_id,
                actor=user.actor,
                action="claim.approved",
                details={
                    "claim_id": str(claim.id),
                    "photo_id": str(claim.photo_id),
                    "guest_user_id": str(claim.user_id),
                    "face_id": str(claim.face_id) if claim.face_id else None,
                },
            )
        else:
            claim.status = "rejected"
            record(
                session,
                event_id=event_id,
                actor=user.actor,
                action="claim.rejected",
                details={
                    "claim_id": str(claim.id),
                    "photo_id": str(claim.photo_id),
                    "guest_user_id": str(claim.user_id),
                    "face_id": str(claim.face_id) if claim.face_id else None,
                },
            )

        return ReviewClaimResponse(claim_id=claim.id, status=claim.status)


def cleanup_expired_attempts(ctx: AppContext) -> int:
    """
    Purpose: prune attempts and scene candidates older than retention days (7 days).
    Inputs: ctx (AppContext).
    Outputs: total number of deleted rows across all events.
    Side effects: deletes expired records within each event's RLS scope.
    """
    days = ctx.settings.attempt_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    total_deleted = 0

    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as session:
        from app.features.events.models import Event

        event_ids = session.scalars(select(Event.id)).all()

    for event_id in event_ids:
        with event_transaction(ctx.sessions, event_id) as session:
            from sqlalchemy import delete

            res1 = session.execute(
                delete(SceneSearchAttempt).where(
                    SceneSearchAttempt.event_id == event_id,
                    SceneSearchAttempt.created_at < cutoff,
                )
            )
            n1 = int(getattr(res1, "rowcount", 0) or 0)
            res2 = session.execute(
                delete(ClaimAttempt).where(
                    ClaimAttempt.event_id == event_id,
                    ClaimAttempt.created_at < cutoff,
                )
            )
            n2 = int(getattr(res2, "rowcount", 0) or 0)
            res3 = session.execute(
                delete(SceneSearchCandidate).where(
                    SceneSearchCandidate.event_id == event_id,
                    SceneSearchCandidate.created_at < cutoff,
                )
            )
            n3 = int(getattr(res3, "rowcount", 0) or 0)
            total_deleted += n1 + n2 + n3

    return total_deleted
