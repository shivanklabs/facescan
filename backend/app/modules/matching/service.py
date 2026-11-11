"""Guest face search: the guest's own face, in one event, with consent, within limits.

Order of checks (cheapest first, nothing biometric before all of them pass):
1. guest search allowed (dev only until a liveness adapter exists);
2. the caller is a guest of the event and holds no staff role (invariant 5);
3. adult age answer and a current face-search consent;
4. the event is searchable;
5. per-user and per-event hourly limits (search_attempts in PostgreSQL; the attempt is
   counted before any processing).
Then every frame (3-5, in memory only) must show exactly one face; the frames must agree on
one cluster. The guest's matches are replaced with the released photo ids. The response and
the audit row never contain selfie data, vectors or scores.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.core.auth import CurrentUser
from app.core.context import AppContext, guest_search_allowed
from app.core.errors import ConflictError, TooManyRequestsError, UnprocessableError
from app.core.policy import load_country_policy as load_country_policy
from app.db.event_scope import event_transaction
from app.modules.audit.service import record
from app.modules.consent.service import (
    check_guest_search_access,
)
from app.modules.events.service import data_key
from app.modules.matching.models import CannotLink, Match, SearchAttempt
from app.modules.vision_pipeline import repository
from app.ports.face_analyzer import ImageRejectedError, SelfieQuery, SelfieRejectedError


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Released photo ids and the 'some photos missing?' hint. Nothing else."""

    release: list[UUID]
    some_photos_missing: bool


def _check_and_count(ctx: AppContext, user: CurrentUser, event_id: UUID) -> None:
    """
    Purpose: run unified search access checks and record search attempt with rate limits.
    Inputs: ctx, user, event_id.
    Outputs: None.
    Side effects: adds SearchAttempt record.
    """
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    with event_transaction(ctx.sessions, event_id) as session:
        check_guest_search_access(session, ctx, event_id, user)
        mine = session.scalar(
            select(func.count())
            .select_from(SearchAttempt)
            .where(SearchAttempt.user_id == user.user_id, SearchAttempt.created_at > hour_ago)
        )
        everyone = session.scalar(
            select(func.count())
            .select_from(SearchAttempt)
            .where(SearchAttempt.event_id == event_id, SearchAttempt.created_at > hour_ago)
        )
        if int(mine or 0) >= ctx.settings.search_per_user_per_hour:
            raise TooManyRequestsError("too many searches; try again later")
        if int(everyone or 0) >= ctx.settings.search_per_event_per_hour:
            raise TooManyRequestsError("this event is busy; try again later")
        session.add(SearchAttempt(event_id=event_id, user_id=user.user_id))


def guest_search(
    ctx: AppContext,
    user: CurrentUser,
    event_id: UUID,
    frames: list[bytes],
    front_only: bool = False,
) -> SearchResult:
    """
    Purpose: search one event with the guest's live selfie frames.
    Inputs: 3-5 encoded frames (memory only), front_only flag. Outputs: SearchResult.
    Side effects: inserts search_attempts; replaces the guest's matches; audit row.
    """
    if not guest_search_allowed(ctx.settings):
        raise HTTPException(status_code=403, detail="search_disabled_without_liveness")
    settings = ctx.settings
    if not settings.search_min_frames <= len(frames) <= settings.search_max_frames:
        raise UnprocessableError(
            f"send {settings.search_min_frames} to {settings.search_max_frames} selfie frames"
        )
    _check_and_count(ctx, user, event_id)

    if ctx.liveness is not None:
        verdict = ctx.liveness.verify_liveness(frames, front_only=front_only)
        if not verdict.passed:
            reasons = ", ".join(verdict.reasons) if verdict.reasons else "liveness_failed"
            raise UnprocessableError(f"liveness check failed: {reasons}")

    queries: list[SelfieQuery] = []
    try:
        for number, frame in enumerate(frames, start=1):
            try:
                queries.append(ctx.analyzer.selfie_query(frame))
            except SelfieRejectedError as exc:
                raise UnprocessableError(
                    f"frame {number}: the selfie must show exactly one face; found {exc.face_count}"
                ) from exc
            except ImageRejectedError as exc:
                raise UnprocessableError(f"frame {number} is not a usable image") from exc
    finally:
        frames.clear()  # drop the frame bytes as soon as they are embedded
    with event_transaction(ctx.sessions, event_id) as session:
        key, _ = data_key(session, ctx.keys, event_id)
        faces, clusters, calibration, versions = repository.load_event_model(session, event_id, key)
        current = ctx.analyzer.engine_versions
        if any(versions.get(k) != v for k, v in current.items() if k in ("auraface", "sface")):
            raise ConflictError("this event is being re-indexed; try again later")
        result = ctx.analyzer.match_frames(
            faces,
            clusters,
            calibration,
            queries,
            front_only=front_only,
            threshold_offset=settings.search_front_only_threshold_offset,
        )
        if not result.release:
            # A borderline result or no-match never releases a photo
            record(
                session,
                event_id=event_id,
                actor=user.actor,
                action="search",
                details={
                    "frames": len(queries),
                    "released": 0,
                    "some_photos_missing": result.some_photos_missing,
                    "frames_agree": result.frames_agree,
                    "front_only": front_only,
                },
            )
            return SearchResult(release=[], some_photos_missing=result.some_photos_missing)

        cannot_links = session.scalars(
            select(CannotLink).where(
                CannotLink.event_id == event_id,
                CannotLink.user_id == user.user_id,
            )
        ).all()
        cannot_photo_ids = {cl.photo_id for cl in cannot_links}

        # Preserve not_me, withdrawn, and claim matches; only replace search matches
        session.execute(
            delete(Match).where(
                Match.event_id == event_id,
                Match.user_id == user.user_id,
                Match.score_band.not_in(["not_me", "withdrawn"]),
                Match.source != "claim",
            )
        )
        # One query for the rows that survived the delete (not_me, withdrawn, claim). Loading
        # them before the delete would skip the re-insert of deleted rows and lose photos.
        existing_matches = {
            m.photo_id: m
            for m in session.scalars(
                select(Match).where(
                    Match.event_id == event_id,
                    Match.user_id == user.user_id,
                )
            ).all()
        }
        excluded_ids = cannot_photo_ids | {
            pid for pid, m in existing_matches.items() if m.score_band in ("not_me", "withdrawn")
        }
        clean_release = [pid for pid in result.release if pid not in excluded_ids]
        now = datetime.now(UTC)
        for photo_id in clean_release:
            existing = existing_matches.get(photo_id)
            if existing is not None:
                if existing.score_band in ("not_me", "withdrawn"):
                    continue
                existing.score_band = "release"
                existing.released_at = now
            else:
                session.add(
                    Match(
                        event_id=event_id,
                        user_id=user.user_id,
                        photo_id=photo_id,
                        score_band="release",
                        released_at=now,
                    )
                )
        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="search",
            details={
                "frames": len(queries),
                "released": len(clean_release),
                "some_photos_missing": result.some_photos_missing,
                "frames_agree": result.frames_agree,
                "front_only": front_only,
            },
        )
    return SearchResult(
        release=clean_release,
        some_photos_missing=result.some_photos_missing,
    )
