"""Integration tests for scene search, preview blurring, 1:1 claims, and rate limits."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
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
from sqlalchemy import func, select, update
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


def test_under_18_blocked_from_scene_search(
    organiser: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: verify that under restricted policy guests answering under 18
        remain blocked from scene search.
    """
    from app.core import policy as policy_module
    from app.core.policy import CountryPolicy
    from app.modules.scene_search import service as scene_service

    monkeypatch.setattr(
        policy_module,
        "load_country_policy",
        lambda *args, **kwargs: CountryPolicy(
            country="IN", minor_age=18, minors_self_search="restricted", search_window_days=30
        ),
    )
    monkeypatch.setattr(
        scene_service,
        "load_country_policy",
        lambda *args, **kwargs: CountryPolicy(
            country="IN", minor_age=18, minors_self_search="restricted", search_window_days=30
        ),
    )
    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(
        ctx, "minor@example.test", event_id, invite_url, adult=False, consent=False
    )

    # Search must be forbidden under restricted policy
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


def test_organiser_claim_queue_and_approve_flow(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify organiser claim queue listing and approval flow:
      1. Pending claim shows photo, face bbox, claimant email (read from users), reason.
      2. Non-organisers (guest) cannot access organiser claims endpoints (403).
      3. Organiser approves claim: moves pending -> approved, releases photo to guest gallery.
      4. Cannot re-review resolved claim (409).
      5. Audit log contains claim.approved.
    """
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1)]
    )
    guest = guest_client(ctx, "claimant_guest@example.test", event_id, invite_url)

    # 1. Guest searches and fails 1:1 claim on photo 2
    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person smiling"},
    )
    candidates = search_resp.json()["candidates"]
    cand_2 = next(c for c in candidates if c["photo_id"] == photos[1] and c["faces"])
    photo_id_2 = cand_2["photo_id"]
    face_id_2 = cand_2["faces"][0]["face_id"]

    frames_fail = [make_selfie_b64(3), make_selfie_b64(3), make_selfie_b64(3)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id_2,
            "face_id": face_id_2,
            "frames": frames_fail,
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["status"] == "queued_for_review"

    # 2. Non-organisers cannot view claim queue (403)
    guest_queue_resp = guest.get(f"/events/{event_id}/claims")
    assert guest_queue_resp.status_code in (403, 404)

    # 3. Organiser lists pending claims
    queue_resp = organiser.get(f"/events/{event_id}/claims")
    assert queue_resp.status_code == 200
    claims_list = queue_resp.json()["claims"]
    assert len(claims_list) == 1
    item = claims_list[0]
    claim_id = item["claim_id"]
    assert item["photo_id"] == photo_id_2
    assert item["face_id"] == face_id_2
    assert item["claimant_email"] == "Guest #1"
    assert item["reason"] == "face_check_failed"
    assert item["status"] == "pending"
    assert item["face_bbox"] is not None
    assert "x" in item["face_bbox"]

    # 4. Guest cannot review claims (403)
    guest_review_resp = guest.post(
        f"/events/{event_id}/claims/{claim_id}/review",
        json={"action": "approve"},
    )
    assert guest_review_resp.status_code in (403, 404)

    # 5. Organiser approves claim
    approve_resp = organiser.post(
        f"/events/{event_id}/claims/{claim_id}/review",
        json={"action": "approve"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"

    # Photo is now released in guest's gallery with source = 'claim'
    gallery_resp = guest.get(f"/events/{event_id}/gallery")
    assert gallery_resp.status_code == 200
    released_photo_ids = [p["photo_id"] for p in gallery_resp.json()["photos"]]
    assert photo_id_2 in released_photo_ids

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        match_row = s.scalar(
            select(Match).where(
                Match.event_id == UUID(event_id),
                Match.photo_id == UUID(photo_id_2),
            )
        )
        assert match_row is not None
        assert match_row.source == "claim"
        assert match_row.score_band == "release"

        from app.modules.audit.models import AuditLog

        audit_row = s.scalar(
            select(AuditLog).where(
                AuditLog.event_id == UUID(event_id),
                AuditLog.action == "claim.approved",
            )
        )
        assert audit_row is not None

    # 6. Re-reviewing already resolved claim returns 409
    dup_review_resp = organiser.post(
        f"/events/{event_id}/claims/{claim_id}/review",
        json={"action": "approve"},
    )
    assert dup_review_resp.status_code == 409


def test_organiser_claim_queue_reject_flow(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify organiser claim rejection flow:
      1. Organiser rejects claim: moves pending -> rejected.
      2. Rejection notice is returned in guest gallery response.
      3. Audit log contains claim.rejected.
    """
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1)]
    )
    guest = guest_client(ctx, "rejected_guest@example.test", event_id, invite_url)

    # 1. Guest searches and fails 1:1 claim on photo 2 (front_only)
    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person standing"},
    )
    candidates = search_resp.json()["candidates"]
    cand_2 = next(c for c in candidates if c["photo_id"] == photos[1] and c["faces"])
    photo_id_2 = cand_2["photo_id"]
    face_id_2 = cand_2["faces"][0]["face_id"]

    frames_fail = [make_selfie_b64(3)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id_2,
            "face_id": face_id_2,
            "frames": frames_fail,
            "front_only": True,
        },
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["status"] == "queued_for_review"

    # Organiser sees reason = front_only_scan
    queue_resp = organiser.get(f"/events/{event_id}/claims")
    item = queue_resp.json()["claims"][0]
    claim_id = item["claim_id"]
    assert item["reason"] == "front_only_scan"

    # 2. Organiser rejects claim
    reject_resp = organiser.post(
        f"/events/{event_id}/claims/{claim_id}/review",
        json={"action": "reject"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # 3. Guest gallery now shows rejection notice in rejections array
    gallery_resp = guest.get(f"/events/{event_id}/gallery")
    assert gallery_resp.status_code == 200
    rejections = gallery_resp.json()["rejections"]
    assert len(rejections) >= 1
    assert any(r["claim_id"] == claim_id for r in rejections)

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        from app.modules.audit.models import AuditLog

        audit_row = s.scalar(
            select(AuditLog).where(
                AuditLog.event_id == UUID(event_id),
                AuditLog.action == "claim.rejected",
            )
        )
        assert audit_row is not None


def test_submit_claim_liveness_failure_creates_no_match_or_claim(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that a failed liveness verdict (passed=False) returns 422
    and creates no Match or Claim records.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP endpoint and database records.
    """
    from app.ports.liveness import LivenessVerdict

    class FakeLivenessFailing:
        def verify_liveness(
            self, frames: list[bytes], front_only: bool = False
        ) -> LivenessVerdict:
            return LivenessVerdict(
                passed=False,
                is_live=False,
                blink_detected=False,
                turn_detected=False,
                silent_face_score=0.0,
                reasons=["spoof_detected"],
            )

    ctx.liveness = FakeLivenessFailing()
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1)]
    )
    guest = guest_client(ctx, "claim_liveness_fail@example.test", event_id, invite_url)

    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person"},
    )
    assert search_resp.status_code == 200
    candidates = search_resp.json()["candidates"]
    photo_id = candidates[0]["photo_id"]
    face_id = candidates[0]["faces"][0]["face_id"]

    frames = [make_selfie_b64(1), make_selfie_b64(1), make_selfie_b64(1)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id,
            "face_id": face_id,
            "frames": frames,
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 422

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        match_count = s.scalar(
            select(func.count(Match.id)).where(Match.event_id == UUID(event_id))
        )
        claim_count = s.scalar(
            select(func.count(Claim.id)).where(Claim.event_id == UUID(event_id))
        )
        assert match_count == 0
        assert claim_count == 0

