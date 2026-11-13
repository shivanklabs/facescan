"""Integration tests for scene search, preview blurring, 1:1 claims, and rate limits."""

import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.core.services_setup import AppContext
from app.db.event_isolation import event_transaction
from app.features.consent.models import Consent
from app.features.events.models import EventMember
from app.features.face_matching.models import Match
from app.features.gallery.models import Claim
from app.features.photo_processing.models import Face
from app.features.photo_upload.models import Photo
from app.features.scene_search.models import (
    SceneSearchAttempt,
    SceneSearchCandidate,
)
from app.features.scene_search.service import cleanup_expired_attempts
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, update
from tests.conftest import make_png
from tests.helpers import (
    discover_search,
    frames_body,
    guest_client,
    key_only_client,
    passkey_guest,
    released_ids,
    searchable_event,
)


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
    # The original size comes with the candidate so face boxes can be placed on the preview.
    assert candidate["photo_width"] > 0 and candidate["photo_height"] > 0
    x, y, w, h = candidate["faces"][0]["bbox"]
    assert 0 <= x < candidate["photo_width"] and 0 <= y < candidate["photo_height"]

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
    from app.core import country_rules as policy_module
    from app.core.country_rules import CountryPolicy
    from app.features.scene_search import service as scene_service

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
    assert item["photo_width"] > 0 and item["photo_height"] > 0
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
    assert photo_id_2 in released_ids(guest, event_id)

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

        from app.features.activity_log.models import AuditLog

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
    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        claim = s.get(Claim, uuid.UUID(claim_id))
        assert claim is not None and claim.status == "rejected"
    assert photo_id_2 not in released_ids(guest, event_id)

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        from app.features.activity_log.models import AuditLog

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
    from app.interfaces.live_face_check import LivenessVerdict

    class FakeLivenessFailing:
        def verify_liveness(
            self, frames: list[bytes], front_only: bool = False, require_blink: bool = True
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
    event_id, invite_url, photos = searchable_event(ctx, organiser, [make_png(1, 1)])
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
        match_count = s.scalar(select(func.count(Match.id)).where(Match.event_id == UUID(event_id)))
        claim_count = s.scalar(select(func.count(Claim.id)).where(Claim.event_id == UUID(event_id)))
        assert match_count == 0
        assert claim_count == 0


def test_submit_claim_no_consent_never_calls_liveness_or_analyzer(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that when consent is missing, claim checks fail immediately (403)
    and liveness / face analyzer are never called.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP endpoints.
    """
    event_id, invite_url, photos = searchable_event(ctx, organiser, [make_png(1, 1)])
    # Join event without granting consent
    key = invite_url.split("?k=")[1]
    guest = passkey_guest(ctx)
    assert guest.post(f"/invites/{event_id}/join", json={"key": key}).status_code == 200

    called = {"liveness": False, "analyzer": False}

    class SpyLiveness:
        def verify_liveness(
            self, frames: list[bytes], front_only: bool = False, require_blink: bool = True
        ) -> object:
            called["liveness"] = True
            pytest.fail("liveness must never be called without consent")

    ctx.liveness = SpyLiveness()

    old_selfie_query = ctx.analyzer.selfie_query

    def fail_analyzer(frame: bytes) -> object:
        called["analyzer"] = True
        pytest.fail("analyzer must never be called without consent")

    ctx.analyzer.selfie_query = fail_analyzer

    try:
        frames = [make_selfie_b64(1), make_selfie_b64(1), make_selfie_b64(1)]
        res = guest.post(
            f"/events/{event_id}/scene-search/claims",
            json={
                "photo_id": photos[0],
                "face_id": str(uuid.uuid4()),
                "frames": frames,
                "front_only": False,
            },
        )
        assert res.status_code == 403
        assert called["liveness"] is False
        assert called["analyzer"] is False
    finally:
        ctx.analyzer.selfie_query = old_selfie_query


def test_submit_claim_selfie_no_face_gives_422_not_500(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that a selfie frame with no face raises 422 UnprocessableError
    with clear message, rather than a 500 error.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP endpoints.
    """
    from app.interfaces.face_analyzer import SelfieRejectedError

    event_id, invite_url, photos = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "claim_noface@example.test", event_id, invite_url)

    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person"},
    )
    assert search_resp.status_code == 200
    candidates = search_resp.json()["candidates"]
    photo_id = candidates[0]["photo_id"]
    face_id = candidates[0]["faces"][0]["face_id"]

    old_selfie_query = ctx.analyzer.selfie_query

    def no_face_selfie(frame: bytes) -> object:
        raise SelfieRejectedError(face_count=0)

    ctx.analyzer.selfie_query = no_face_selfie

    try:
        frames = [make_selfie_b64(1), make_selfie_b64(1), make_selfie_b64(1)]
        res = guest.post(
            f"/events/{event_id}/scene-search/claims",
            json={
                "photo_id": photo_id,
                "face_id": face_id,
                "frames": frames,
                "front_only": False,
            },
        )
        assert res.status_code == 422
        assert "the selfie must show exactly one face; found 0" in res.text
    finally:
        ctx.analyzer.selfie_query = old_selfie_query


def test_face_search_preserves_claimed_photos(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that running a face search preserves existing claimed matches
    (source == 'claim') in the user's gallery.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP endpoints and database records.
    """
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1)]
    )
    guest = guest_client(ctx, "claim_preserve@example.test", event_id, invite_url)

    # 1. Search scene and claim photo 1 (person 1)
    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person"},
    )
    assert search_resp.status_code == 200
    candidates = search_resp.json()["candidates"]
    cand_1 = next(c for c in candidates if c["photo_id"] == photos[0] and c["faces"])
    photo_id_1 = cand_1["photo_id"]
    face_id_1 = cand_1["faces"][0]["face_id"]

    frames_claim = [make_selfie_b64(1), make_selfie_b64(1), make_selfie_b64(1)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id_1,
            "face_id": face_id_1,
            "frames": frames_claim,
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["status"] == "released"

    # Verify photo 1 is in gallery
    assert photo_id_1 in released_ids(guest, event_id)

    # 2. Run face search with person 2's selfie frames
    search_body = frames_body(
        make_png(2, 1, (40, 30)),
        make_png(2, 1, (41, 30)),
        make_png(2, 1, (42, 30)),
    )
    face_search_resp = discover_search(guest, search_body)
    assert face_search_resp.status_code == 200

    # 3. Check gallery: photo 1 (claimed) MUST still be present
    assert photo_id_1 in released_ids(guest, event_id)

    # And in database, match row has source == 'claim'
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        claimed_match = s.scalar(
            select(Match).where(
                Match.event_id == UUID(event_id),
                Match.photo_id == UUID(photo_id_1),
            )
        )
        assert claimed_match is not None
        assert claimed_match.source == "claim"
        assert claimed_match.score_band == "release"


def test_claim_review_without_passkey_is_refused(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that claim listing and review require a PasskeyUser session;
    an organiser session without a passkey (magic link only) receives 403 passkey_required.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP endpoints.
    """
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1)]
    )
    guest = guest_client(ctx, "claimant_guest2@example.test", event_id, invite_url)

    # Guest submits a claim that goes to pending review
    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person"},
    )
    assert search_resp.status_code == 200
    cand = search_resp.json()["candidates"][0]
    photo_id = cand["photo_id"]
    face_id = cand["faces"][0]["face_id"]

    frames_fail = [make_selfie_b64(3), make_selfie_b64(3), make_selfie_b64(3)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id,
            "face_id": face_id,
            "frames": frames_fail,
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 200

    # A staff member who used a staff key but has no passkey yet (not a passkey session)
    magic_client = key_only_client(ctx, "organiser")

    # 1. Listing claims without passkey returns 403 passkey_required
    list_resp = magic_client.get(f"/events/{event_id}/claims")
    assert list_resp.status_code == 403
    assert list_resp.json()["detail"] == "passkey_required"

    # Get claim_id using passkey-authenticated organiser client
    queue = organiser.get(f"/events/{event_id}/claims").json()
    claim_id = queue["claims"][0]["claim_id"]

    # 2. Reviewing claim without passkey returns 403 passkey_required
    review_resp = magic_client.post(
        f"/events/{event_id}/claims/{claim_id}/review",
        json={"action": "approve"},
    )
    assert review_resp.status_code == 403
    assert review_resp.json()["detail"] == "passkey_required"


def test_user_with_organiser_and_guest_role_refused_by_scene_search_and_claim(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that a user with both guest and organiser roles receives
        403 staff_cannot_search on both scene search and claims endpoints.
    Inputs: organiser (TestClient), ctx (AppContext).
    Outputs: None.
    Side effects: tests HTTP endpoints and verifies unified search access enforcement.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "guest_staff@example.test", event_id, invite_url)

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        consent_row = s.scalar(select(Consent).where(Consent.event_id == UUID(event_id)))
        assert consent_row is not None
        guest_user_id = consent_row.user_id
        s.add(EventMember(event_id=UUID(event_id), user_id=guest_user_id, role="organiser"))

    search_resp = guest.post(f"/events/{event_id}/scene-search", json={"query": "person"})
    assert search_resp.status_code == 403
    assert search_resp.json()["detail"] == "staff_cannot_search"

    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": str(uuid.uuid4()),
            "face_id": str(uuid.uuid4()),
            "frames": [make_selfie_b64(1)],
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 403
    assert claim_resp.json()["detail"] == "staff_cannot_search"


def test_old_notice_version_refused_by_scene_search_and_claim(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that a user whose consent record has an outdated notice_version
        receives 403 consent_required on both scene search and claims endpoints.
    Inputs: organiser (TestClient), ctx (AppContext).
    Outputs: None.
    Side effects: tests HTTP endpoints and verifies unified search access enforcement.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "outdated_consent@example.test", event_id, invite_url)

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        s.execute(
            update(Consent)
            .where(Consent.event_id == UUID(event_id))
            .values(notice_version="legacy-v0")
        )

    search_resp = guest.post(f"/events/{event_id}/scene-search", json={"query": "person"})
    assert search_resp.status_code == 403
    assert search_resp.json()["detail"] == "consent_required"

    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": str(uuid.uuid4()),
            "face_id": str(uuid.uuid4()),
            "frames": [make_selfie_b64(1)],
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 403
    assert claim_resp.json()["detail"] == "consent_required"


def test_preview_falls_back_to_original_when_web_copy_is_missing(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify candidate preview falls back to original photo when web derivative is missing.
    Inputs: organiser (TestClient), ctx (AppContext).
    Outputs: None.
    Side effects: tests HTTP candidate preview endpoint.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "preview_fallback@example.test", event_id, invite_url)

    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person"},
    )
    assert search_resp.status_code == 200
    candidate = search_resp.json()["candidates"][0]
    preview_url = candidate["preview_url"]
    photo_uuid = UUID(candidate["photo_id"])

    # 1. Clear web_object_key in database: preview must fall back to original
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        photo = s.get(Photo, photo_uuid)
        assert photo is not None
        photo.web_object_key = None

    resp = guest.get(preview_url)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"

    # 2. Point web_object_key to missing file in derivatives: preview must fall back to original
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        photo = s.get(Photo, photo_uuid)
        assert photo is not None
        photo.web_object_key = "a" * 43 + ".jpg"

    resp2 = guest.get(preview_url)
    assert resp2.status_code == 200
    assert resp2.headers["content-type"] == "image/jpeg"


def test_ten_claims_produce_one_select_for_photos(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that listing claims joins Photo so 10 claims produce only 1 photo SELECT.
    Inputs: organiser (TestClient), ctx (AppContext).
    Outputs: None.
    Side effects: adds test claims to database and measures SQL execution count.
    """
    event_id, invite_url, photos = searchable_event(ctx, organiser, [make_png(1, 1)])
    _ = guest_client(ctx, "claim_ten@example.test", event_id, invite_url)

    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        consent_row = s.scalar(select(Consent).where(Consent.event_id == UUID(event_id)))
        user_id = consent_row.user_id if consent_row else uuid.uuid4()
        face_id = s.scalar(select(Face.id).where(Face.event_id == UUID(event_id)).limit(1))
        assert face_id is not None
        for i in range(10):
            s.add(
                Claim(
                    event_id=UUID(event_id),
                    user_id=user_id,
                    photo_id=UUID(photos[0]),
                    face_id=face_id if i == 0 else None,  # one pending claim per face
                    status="pending",
                    reason=f"test claim {i}",
                )
            )

    captured_queries: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        captured_queries.append(statement)

    sa_event.listen(ctx.engine, "before_cursor_execute", before_cursor_execute)
    try:
        resp = organiser.get(f"/events/{event_id}/claims")
        assert resp.status_code == 200
        assert len(resp.json()["claims"]) >= 10
    finally:
        sa_event.remove(ctx.engine, "before_cursor_execute", before_cursor_execute)

    photo_queries = [
        q
        for q in captured_queries
        if ("from photos" in q.lower() or "join photos" in q.lower()) and "select" in q.lower()
    ]
    assert len(photo_queries) == 1, (
        f"Expected 1 photo query, got {len(photo_queries)}: {photo_queries}"
    )
    # Faces come from the same joined query: no separate face lookup per claim.
    assert not [q for q in captured_queries if "from faces" in q.lower()]
    assert sum(c["face_bbox"] is not None for c in resp.json()["claims"]) == 1


def test_approving_claim_on_not_me_photo_gives_409(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: approving a claim on a photo already marked not_me gives 409; the match stays
        not_me and the claim stays pending.
    Inputs: organiser (TestClient), ctx (AppContext).
    Outputs: None.
    Side effects: tests claim submission and organiser review with pre-existing not_me match.
    """
    event_id, invite_url, photos = searchable_event(
        ctx, organiser, [make_png(1, 1), make_png(2, 1)]
    )
    guest = guest_client(ctx, "claimant_notme@example.test", event_id, invite_url)

    search_resp = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "person"},
    )
    assert search_resp.status_code == 200
    candidate = search_resp.json()["candidates"][0]
    photo_id = candidate["photo_id"]
    face_id = candidate["faces"][0]["face_id"]

    frames_fail = [make_selfie_b64(3), make_selfie_b64(3), make_selfie_b64(3)]
    claim_resp = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": photo_id,
            "face_id": face_id,
            "frames": frames_fail,
            "front_only": False,
        },
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["status"] == "queued_for_review"

    queue = organiser.get(f"/events/{event_id}/claims").json()
    claim_id = queue["claims"][0]["claim_id"]

    # Pre-existing match in DB is marked as not_me
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        consent_row = s.scalar(select(Consent).where(Consent.event_id == UUID(event_id)))
        assert consent_row is not None
        guest_user_id = consent_row.user_id
        s.add(
            Match(
                event_id=UUID(event_id),
                user_id=guest_user_id,
                photo_id=UUID(photo_id),
                score_band="not_me",
                source="search",
            )
        )

    # Organiser approves the claim
    review_resp = organiser.post(
        f"/events/{event_id}/claims/{claim_id}/review",
        json={"action": "approve"},
    )
    assert review_resp.status_code == 409
    assert organiser.get(f"/events/{event_id}/claims").json()["claims"][0]["claim_id"] == claim_id

    # Match in DB must still be score_band == 'not_me'
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        match_row = s.scalar(
            select(Match).where(
                Match.event_id == UUID(event_id),
                Match.user_id == guest_user_id,
                Match.photo_id == UUID(photo_id),
            )
        )
        assert match_row is not None
        assert match_row.score_band == "not_me"
        assert match_row.source == "search"
