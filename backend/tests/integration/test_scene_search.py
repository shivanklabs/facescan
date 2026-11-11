"""Integration tests for scene search, preview blurring, 1:1 claims, and rate limits."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.context import AppContext
from app.db.event_scope import event_transaction
from app.modules.gallery.models import Claim
from app.modules.matching.models import Match
from app.modules.scene_search.models import (
    SceneSearchAttempt,
    SceneSearchCandidate,
)
from app.modules.scene_search.service import cleanup_expired_attempts
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from tests.conftest import make_png
from tests.helpers import guest_client, searchable_event


def make_selfie_b64(person: int = 1) -> str:
    """Purpose: return base64 encoded selfie frame PNG."""
    raw = make_png(person, 1, (40, 30))
    return base64.b64encode(raw).decode("utf-8")


def test_scene_search_daily_limit_concurrent_enforcement(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that PostgreSQL advisory transaction lock serializes parallel requests
        and strictly enforces the daily limit without race conditions.
    """
    ctx.settings.scene_search_per_guest_per_day = 3
    event_id, invite_url, _ = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1), make_png(3, 1)]
    )

    guest = guest_client(ctx, "guest_limit@example.test", event_id, invite_url)

    # Send 8 concurrent scene search requests
    def send_search() -> int:
        resp = guest.post(
            f"/events/{event_id}/scene-search",
            json={"query": "person in blue shirt"},
        )
        return resp.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: send_search(), range(8)))

    assert statuses.count(200) == 3, f"Expected exactly 3 successes, got {statuses}"
    assert statuses.count(429) == 5, f"Expected exactly 5 429s, got {statuses}"


def test_scene_search_event_isolation(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that scene search only ever returns photos from the target event,
        never leaking candidates across events.
    """
    event_a, invite_a, photos_a = searchable_event(ctx, organiser, [make_png(1, 1)], name="Event A")
    event_b, invite_b, photos_b = searchable_event(ctx, organiser, [make_png(2, 1)], name="Event B")

    guest_a = guest_client(ctx, "guest_a@example.test", event_a, invite_a)

    resp_a = guest_a.post(
        f"/events/{event_a}/scene-search",
        json={"query": "people smiling outdoors"},
    )
    assert resp_a.status_code == 200
    candidates_a = resp_a.json()["candidates"]

    # Candidate photo IDs must belong to Event A, never Event B
    candidate_pids = {c["photo_id"] for c in candidates_a}
    assert photos_a[0] in candidate_pids
    assert photos_b[0] not in candidate_pids


def test_candidate_preview_signature_and_history_check(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify HMAC signature and recent candidate history checks on preview URLs.
    """
    event_id, invite_url, photos = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "preview_guest@example.test", event_id, invite_url)

    # Search to generate server candidate history
    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "stage photos"},
    )
    assert search_resp.status_code == 200
    candidate = search_resp.json()["candidates"][0]
    preview_url = candidate["preview_url"]

    # 1. Valid preview URL succeeds
    resp = guest.get(preview_url)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "private, no-store"

    # 2. Tampered signature fails
    tampered_url = preview_url.replace("signature=", "signature=bad_signature_")
    assert guest.get(tampered_url).status_code == 403

    # 3. Expired timestamp fails
    expired_url = preview_url.replace("expires=", "expires=1000&old_expires=")
    assert guest.get(expired_url).status_code == 403


def test_submit_claim_pass_and_fail_flow(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify 1:1 claim flow:
    - Pass releases photo to gallery with source = 'claim'
    - Fail queues claim in 'pending' status for organiser review
    - Repeated claim while pending raises 409 Conflict
    """
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1)]
    )
    guest = guest_client(ctx, "claim_guest@example.test", event_id, invite_url)

    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person at table"},
    )
    assert search_resp.status_code == 200
    candidates = search_resp.json()["candidates"]
    cand_1 = next(c for c in candidates if c["photo_id"] == photos[0] and c["faces"])
    photo_id_1 = cand_1["photo_id"]
    face_id_1 = cand_1["faces"][0]["face_id"]

    # 1. Matching face: person 1 claiming person 1's photo
    frames_matching = [make_selfie_b64(1), make_selfie_b64(1), make_selfie_b64(1)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id_1,
            "face_id": face_id_1,
            "frames": frames_matching,
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["status"] == "released"

    # Verify photo released in gallery with source='claim'
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        match_row = s.scalar(select(Match).where(Match.photo_id == UUID(photo_id_1)))
        assert match_row is not None
        assert match_row.source == "claim"
        assert match_row.score_band == "release"

    # 2. Mismatched face: person 3 claiming person 2's photo
    cand_2 = next(c for c in candidates if c["photo_id"] == photos[1] and c["faces"])
    photo_id_2 = cand_2["photo_id"]
    face_id_2 = cand_2["faces"][0]["face_id"]

    frames_stranger = [make_selfie_b64(3), make_selfie_b64(3), make_selfie_b64(3)]
    fail_claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id_2,
            "face_id": face_id_2,
            "frames": frames_stranger,
            "front_only": False,
        },
    )
    assert fail_claim_resp.status_code == 200
    assert fail_claim_resp.json()["status"] == "queued_for_review"

    # Verify claim is pending in claims table
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        claim_row = s.scalar(select(Claim).where(Claim.face_id == UUID(face_id_2)))
        assert claim_row is not None
        assert claim_row.status == "pending"
        assert claim_row.reason == "face_check_failed"

    # 3. Duplicate pending claim raises 409
    dup_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id_2,
            "face_id": face_id_2,
            "frames": frames_stranger,
            "front_only": False,
        },
    )
    assert dup_resp.status_code == 409


def test_under_18_blocked_from_scene_search(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that guests answering under 18 remain strictly blocked from scene search.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(
        ctx, "minor@example.test", event_id, invite_url, adult=False, consent=False
    )

    # Search must be forbidden
    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "people in room"},
    )
    assert search_resp.status_code == 403


def test_worker_retention_pruning(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that cleanup_expired_attempts prunes attempts and candidates older than 7 days.
    """
    event_id, invite_url, photos = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "retention_guest@example.test", event_id, invite_url)

    # Run search to create attempt and candidate
    guest.post(f"/events/{event_id}/scene-search", json={"query": "party"})

    # Backdate rows to 8 days ago
    eight_days_ago = datetime.now(UTC) - timedelta(days=8)
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        s.execute(
            update(SceneSearchAttempt)
            .where(SceneSearchAttempt.event_id == UUID(event_id))
            .values(created_at=eight_days_ago)
        )
        s.execute(
            update(SceneSearchCandidate)
            .where(SceneSearchCandidate.event_id == UUID(event_id))
            .values(created_at=eight_days_ago)
        )

    # Run cleanup
    deleted = cleanup_expired_attempts(ctx)
    assert deleted >= 2

    # Verify rows were pruned
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        attempts_left = s.scalars(
            select(SceneSearchAttempt).where(SceneSearchAttempt.event_id == UUID(event_id))
        ).all()
        candidates_left = s.scalars(
            select(SceneSearchCandidate).where(SceneSearchCandidate.event_id == UUID(event_id))
        ).all()
        assert len(attempts_left) == 0
        assert len(candidates_left) == 0
