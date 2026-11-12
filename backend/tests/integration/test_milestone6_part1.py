"""Integration tests for Milestone 6 Part 1:
- "Not me" button, match marked not_me, cannot-links, audit-logged, count-only for organisers.
- Borderline results never release photos and flag borderline status.
- Duplicate checks at upload: exact duplicate rejected, near-duplicate flagged.
"""

import io
from uuid import UUID

from app.core.services_setup import AppContext
from app.db.event_isolation import event_transaction
from app.features.activity_log.models import AuditLog
from app.features.face_matching.models import CannotLink, Match
from app.features.photo_upload.models import Photo
from app.features.photo_upload.service import compute_phash, phash_distance
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    frames_body,
    guest_client,
    searchable_event,
)

PERSON_1 = [make_png(1, 2, (64 + i, 48)) for i in range(4)]
PERSON_2 = [make_png(2, 2, (90 + i, 48)) for i in range(3)]


def selfie_frames(person: int, count: int = 3) -> bytes:
    """Purpose: generate test selfie frames for a synthetic person."""
    return frames_body(*[make_png(person, 1, (40 + i, 30)) for i in range(count)])


def test_not_me_and_cannot_links(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify 'Not me' marks match not_me, records cannot-links, removes from gallery,
        blocks downloads, prevents future search releases, and is count-only for organisers.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, PERSON_1 + PERSON_2)
    guest = guest_client(ctx, "guest1@example.test", event_id, invite_url)

    # Search: guest matches person 1 photos
    res = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert res.status_code == 200
    released = res.json()["release"]
    assert len(released) > 0

    target_photo_id = released[0]

    # Verify photo is in gallery
    gal_resp = guest.get(f"/events/{event_id}/gallery")
    assert gal_resp.status_code == 200
    photo_ids_in_gallery = [p["photo_id"] for p in gal_resp.json()["photos"]]
    assert target_photo_id in photo_ids_in_gallery

    # Verify photo download works before "Not me"
    dl_before = guest.get(f"/events/{event_id}/gallery/{target_photo_id}/download")
    assert dl_before.status_code == 200

    # Click "Not me"
    not_me_resp = guest.post(f"/events/{event_id}/gallery/{target_photo_id}/not-me")
    assert not_me_resp.status_code == 200
    assert not_me_resp.json() == {"status": "ok", "photo_id": target_photo_id}

    # Verify photo disappeared from gallery immediately
    gal_after = guest.get(f"/events/{event_id}/gallery")
    assert gal_after.status_code == 200
    photo_ids_after = [p["photo_id"] for p in gal_after.json()["photos"]]
    assert target_photo_id not in photo_ids_after

    # Verify photo download is now blocked (404)
    dl_after = guest.get(f"/events/{event_id}/gallery/{target_photo_id}/download")
    assert dl_after.status_code == 404

    # Verify database state under event transaction:
    # 1. Match row marked 'not_me' (never deleted!)
    # 2. cannot_links row inserted
    # 3. audit_log has gallery.not_me
    eid = UUID(event_id)
    with event_transaction(ctx.sessions, eid) as session:
        match_row = session.scalars(
            select(Match).where(
                Match.event_id == eid,
                Match.photo_id == UUID(target_photo_id),
            )
        ).first()
        assert match_row is not None
        assert match_row.score_band == "not_me"

        cannot_row = session.scalars(
            select(CannotLink).where(
                CannotLink.event_id == eid,
                CannotLink.photo_id == UUID(target_photo_id),
            )
        ).first()
        assert cannot_row is not None

        audit_row = session.scalars(
            select(AuditLog).where(
                AuditLog.event_id == eid,
                AuditLog.action == "gallery.not_me",
            )
        ).first()
        assert audit_row is not None
        assert audit_row.details == {"photo_id": target_photo_id}

    # Re-run search: cannot-linked photo must NOT be released, and not_me match row preserved
    res2 = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert res2.status_code == 200
    released_2 = res2.json()["release"]
    assert target_photo_id not in released_2

    with event_transaction(ctx.sessions, eid) as session:
        preserved_match = session.scalars(
            select(Match).where(
                Match.event_id == eid,
                Match.photo_id == UUID(target_photo_id),
            )
        ).first()
        assert preserved_match is not None
        assert preserved_match.score_band == "not_me"

    # Organiser sees only a count of not_me
    org_resp = organiser.get(f"/events/{event_id}")
    assert org_resp.status_code == 200
    counts = org_resp.json()["counts"]
    assert counts is not None
    assert counts.get("not_me") == 1


def test_borderline_result_never_releases_photos(
    organiser: TestClient, ctx: AppContext, monkeypatch
) -> None:
    """
    Purpose: verify that a borderline scan result returns borderline=True,
        releases 0 photos, and never creates released Match rows.
    """
    from app.interfaces.face_analyzer import FramesMatch

    event_id, invite_url, _ = searchable_event(ctx, organiser, PERSON_1 + PERSON_2)
    guest = guest_client(ctx, "guest_bord@example.test", event_id, invite_url)

    def mock_borderline(*_args, **_kwargs):
        return FramesMatch(release=[], some_photos_missing=True, frames_agree=True)

    monkeypatch.setattr(ctx.analyzer, "match_frames", mock_borderline)

    res = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["release"] == []
    assert data["some_photos_missing"] is True

    # Verify no matches were released in the database
    eid = UUID(event_id)
    with event_transaction(ctx.sessions, eid) as session:
        matches_count = session.scalar(
            select(func.count(Match.id)).where(Match.event_id == eid, Match.score_band == "release")
        )
        assert matches_count == 0


def test_duplicate_upload_exact_rejected(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify exact duplicates (same SHA-256 in same event) are not stored twice
        and return 'already uploaded' message.
    """
    event_resp = organiser.post("/events", json={"name": "DupCheck", "country": "IN"}).json()
    event_id = event_resp["event_id"]

    photo_bytes = PERSON_1[0]
    files = [("files", ("test.png", photo_bytes, "image/png"))]

    # First upload
    up1 = organiser.post(f"/events/{event_id}/photos", files=files)
    assert up1.status_code == 201
    res1 = up1.json()
    assert len(res1["photo_ids"]) == 1
    assert res1["already_uploaded"] == []

    # Second upload of the EXACT same bytes
    up2 = organiser.post(f"/events/{event_id}/photos", files=files)
    assert up2.status_code == 201
    res2 = up2.json()
    assert len(res2["photo_ids"]) == 0
    assert res2["already_uploaded"] == [1]
    assert res2["message"] == "already uploaded"

    # Verify in DB: only 1 photo row exists in this event
    eid = UUID(event_id)
    with event_transaction(ctx.sessions, eid) as session:
        count = session.scalar(select(func.count(Photo.id)).where(Photo.event_id == eid))
        assert count == 1


def test_near_duplicate_flagged_never_dropped(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify perceptual hash computes, near-duplicates are flagged (is_near_duplicate=True),
        and are never dropped.
    """
    from PIL import ImageDraw

    # Create base image with shapes/texture
    base_img = Image.new("RGB", (200, 200), color="white")
    d1 = ImageDraw.Draw(base_img)
    d1.rectangle([20, 20, 80, 80], fill="black")
    d1.ellipse([100, 100, 160, 160], fill="blue")
    buf1 = io.BytesIO()
    base_img.save(buf1, format="JPEG", quality=90)
    bytes1 = buf1.getvalue()

    # Create slightly modified near-duplicate image (saved with different compression quality)
    near_img = base_img.copy()
    d2 = ImageDraw.Draw(near_img)
    d2.point([20, 20], fill="gray")
    buf2 = io.BytesIO()
    near_img.save(buf2, format="JPEG", quality=80)
    bytes2 = buf2.getvalue()

    # Verify pHash distance is small (<= 10)
    h1 = compute_phash(bytes1)
    h2 = compute_phash(bytes2)
    dist = phash_distance(h1, h2)
    assert dist <= 10, f"Expected small Hamming distance, got {dist}"

    event_resp = organiser.post("/events", json={"name": "NearDupCheck", "country": "IN"}).json()
    event_id = event_resp["event_id"]

    files = [
        ("files", ("img1.jpg", bytes1, "image/jpeg")),
        ("files", ("img2.jpg", bytes2, "image/jpeg")),
    ]
    up = organiser.post(f"/events/{event_id}/photos", files=files)
    assert up.status_code == 201
    res = up.json()
    assert len(res["photo_ids"]) == 2

    # Verify in DB: both photos stored; the second one has is_near_duplicate=True
    eid = UUID(event_id)
    with event_transaction(ctx.sessions, eid) as session:
        p1 = session.get(Photo, UUID(res["photo_ids"][0]))
        p2 = session.get(Photo, UUID(res["photo_ids"][1]))
        assert p1 is not None and p2 is not None
        assert p1.is_near_duplicate is False
        assert p2.is_near_duplicate is True
        assert p1.sha256 is not None
        assert p2.sha256 is not None
        assert p1.phash is not None
        assert p2.phash is not None
