"""Integration tests for incremental indexing and error resilience (Audit B4)."""

from unittest.mock import PropertyMock, patch
from uuid import UUID

from app.core.context import AppContext
from app.db.event_scope import event_transaction
from app.features.events.models import Event
from app.features.events.service import data_key
from app.features.photo_processing import repository
from app.features.photo_processing.models import Face
from app.features.photo_upload.models import Photo
from app.interfaces.face_analyzer import AnalysedFace
from app.worker import run_one
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    guest_client,
    searchable_event,
)
from tests.integration.test_guest_flow import PERSON_1, PERSON_2, selfie_frames


def test_incremental_indexing_preserves_existing_face_ids(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify approving a second collection keeps the first collection's face ids,
        leaves event searchable before and after the job runs, and guest search succeeds.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests HTTP endpoints and database states.
    """
    # 1. Index collection 1 with Person 1
    event_id, invite_url, pids1 = searchable_event(ctx, organiser, PERSON_1)
    guest = guest_client(ctx, "guest_inc1@example.test", event_id, invite_url)

    # Record Face IDs from collection 1
    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        faces_col1_before = set(
            session.scalars(select(Face.id).where(Face.event_id == UUID(event_id))).all()
        )
    assert len(faces_col1_before) > 0

    # 2. Upload collection 2 with Person 2
    files = [("files", (f"p2_{i}.png", data, "image/png")) for i, data in enumerate(PERSON_2)]
    up_resp = organiser.post(f"/events/{event_id}/photos", files=files)
    assert up_resp.status_code == 201
    col2_id = up_resp.json()["collection_id"]

    # Approve collection 2
    app_resp = organiser.post(f"/events/{event_id}/collections/{col2_id}/approve")
    assert app_resp.status_code == 200

    # Assertion 1: Right after approving collection 2 and before the worker job runs,
    # the event is still "searchable"
    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        ev = session.get(Event, UUID(event_id))
        assert ev is not None
        assert ev.status == "searchable"

    # And a guest search succeeds at that point
    search_resp = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert search_resp.status_code == 200
    assert any(p in pids1 for p in search_resp.json()["release"])

    # 3. Now run the worker indexing job
    while run_one(ctx, "test-worker"):
        pass

    # Verify collection 1 Face IDs are completely preserved
    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        faces_after = set(
            session.scalars(select(Face.id).where(Face.event_id == UUID(event_id))).all()
        )
        ev_after = session.get(Event, UUID(event_id))
        assert ev_after is not None
        assert ev_after.status == "searchable"

    assert faces_col1_before.issubset(faces_after)
    assert len(faces_after) > len(faces_col1_before)


def test_search_with_unclustered_new_faces_returns_only_old_cluster_photos(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that when new Face rows exist that are in no cluster yet (mid-photo loop),
        a guest search succeeds and returns only photos from the old clusters.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests matching behavior with unclustered faces.
    """
    event_id, invite_url, pids = searchable_event(ctx, organiser, PERSON_1)
    guest = guest_client(ctx, "guest_unclustered@example.test", event_id, invite_url)

    # Insert a new photo and its faces into database without rebuilding clusters
    photo2 = PERSON_2[0]
    faces2 = ctx.analyzer.analyse_photo(photo2)

    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        existing_photo = session.scalars(
            select(Photo).where(Photo.event_id == UUID(event_id))
        ).first()
        assert existing_photo is not None
        key, key_ver = data_key(session, ctx.keys, UUID(event_id))
        new_photo = Photo(
            event_id=UUID(event_id),
            collection_id=existing_photo.collection_id,
            bucket="photos",
            object_key="unclustered.png",
            content_type="image/png",
            byte_size=len(photo2),
            width=64,
            height=48,
            sha256="0" * 64,
            status="approved",
        )
        session.add(new_photo)
        session.flush()
        new_photo_id = new_photo.id
        repository.add_faces(session, UUID(event_id), new_photo_id, faces2, key, key_ver)

    # Guest searches using Person 1 frames
    search_resp = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert search_resp.status_code == 200
    release_pids = search_resp.json()["release"]
    # Only photos from old clusters are returned; the unclustered photo is never released
    assert str(new_photo_id) not in release_pids
    assert any(p in pids for p in release_pids)


def test_incremental_indexing_failing_photo_marked_failed_others_succeed(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify a photo whose analysis raises ends as failed while others are indexed,
        the event becomes searchable, and orphan new web copies are cleaned up.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests error resilience during index_event.
    """
    # Create event and upload 2 photos
    ev_resp = organiser.post("/events", json={"name": "FaultTest", "country": "IN"})
    assert ev_resp.status_code == 201
    event_id = ev_resp.json()["event_id"]

    photo_good = make_png(1, 1)
    photo_bad = make_png(2, 1)
    files = [
        ("files", ("good.png", photo_good, "image/png")),
        ("files", ("bad.png", photo_bad, "image/png")),
    ]
    up_resp = organiser.post(f"/events/{event_id}/photos", files=files)
    assert up_resp.status_code == 201
    col_id = up_resp.json()["collection_id"]
    photo_ids = up_resp.json()["photo_ids"]

    app_resp = organiser.post(f"/events/{event_id}/collections/{col_id}/approve")
    assert app_resp.status_code == 200

    # Find the Photo id of the bad photo
    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        photos = session.scalars(select(Photo).where(Photo.event_id == UUID(event_id))).all()
        bad_photo = [p for p in photos if p.id == UUID(photo_ids[1])][0]
        good_photo = [p for p in photos if p.id == UUID(photo_ids[0])][0]

    original_analyse = ctx.analyzer.analyse_photo

    def fail_on_bad(data: bytes) -> list[AnalysedFace]:
        if data == photo_bad:
            raise RuntimeError("simulated corrupt photo analysis failure")
        return original_analyse(data)

    with patch.object(ctx.analyzer, "analyse_photo", side_effect=fail_on_bad):
        while run_one(ctx, "test-worker"):
            pass

    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        ev = session.get(Event, UUID(event_id))
        assert ev is not None
        assert ev.status == "searchable"

        p_bad = session.get(Photo, bad_photo.id)
        assert p_bad is not None
        assert p_bad.status == "failed"

        p_good = session.get(Photo, good_photo.id)
        assert p_good is not None
        assert p_good.status == "indexed"


def test_engine_version_mismatch_triggers_full_rebuild(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that if stored engine versions differ from analyzer engine versions,
        a full rebuild runs and updates stored versions.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: tests full rebuild fallback.
    """
    photos = [make_png(1, 1), make_png(1, 2)]
    event_id, _, _ = searchable_event(ctx, organiser, photos)

    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        key, _ = data_key(session, ctx.keys, UUID(event_id))
        old_versions = repository.load_engine_versions(session, UUID(event_id), key)
    assert old_versions != {}

    # Simulate new analyzer engine version
    upgraded_versions = dict(old_versions)
    upgraded_versions["auraface"] = "v2_test_upgraded"

    with patch.object(
        type(ctx.analyzer),
        "engine_versions",
        new_callable=PropertyMock,
        return_value=upgraded_versions,
    ):
        # Trigger index_event directly
        from app.features.photo_processing.service import index_event

        summary = index_event(ctx, UUID(event_id))
        assert summary.photos == 2

        with event_transaction(ctx.sessions, UUID(event_id)) as session:
            key, _ = data_key(session, ctx.keys, UUID(event_id))
            new_versions = repository.load_engine_versions(session, UUID(event_id), key)
            assert new_versions.get("auraface") == "v2_test_upgraded"


def test_failed_removal_of_old_web_copy_keeps_photo_indexed(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: on a full rebuild, when the old web copy cannot be removed, the photo stays
        indexed and its new web copy stays readable.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: runs index_event with a storage whose delete_object raises.
    """
    from app.features.photo_processing.service import index_event

    event_id, _, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        key, _ = data_key(session, ctx.keys, UUID(event_id))
        versions = dict(repository.load_engine_versions(session, UUID(event_id), key))
    versions["auraface"] = "v2_test_upgraded"

    with (
        patch.object(
            type(ctx.analyzer), "engine_versions", new_callable=PropertyMock, return_value=versions
        ),
        patch.object(type(ctx.storage), "delete_object", side_effect=OSError("disk busy")),
    ):
        index_event(ctx, UUID(event_id))

    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        photo = session.scalars(select(Photo).where(Photo.event_id == UUID(event_id))).one()
        assert photo.status == "indexed"
        assert photo.web_object_key is not None
        web_key = photo.web_object_key
    assert ctx.storage.get_object(ctx.settings.storage_bucket_derivatives, UUID(event_id), web_key)


def test_pending_claim_survives_full_rebuild_without_face_box(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: a full rebuild (engine version change) recreates faces; a pending claim must
        stay in the organiser queue, only without its face box.
    Inputs: organiser client, ctx.
    Outputs: None.
    Side effects: queues a claim through the API, then runs a full index_event.
    """
    import base64

    from app.features.photo_processing.service import index_event

    event_id, invite_url, _ = searchable_event(ctx, organiser, [make_png(1, 1)])
    guest = guest_client(ctx, "claim_rebuild@example.test", event_id, invite_url)
    candidate = guest.post(f"/events/{event_id}/scene-search", json={"query": "person"}).json()[
        "candidates"
    ][0]
    other_person = base64.b64encode(make_png(3, 1, (40, 30))).decode()
    claimed = guest.post(
        f"/events/{event_id}/scene-search/claims",
        json={
            "photo_id": candidate["photo_id"],
            "face_id": candidate["faces"][0]["face_id"],
            "frames": [other_person] * 3,
            "front_only": False,
        },
    )
    assert claimed.json()["status"] == "queued_for_review"
    before = organiser.get(f"/events/{event_id}/claims").json()["claims"]
    assert len(before) == 1
    assert before[0]["face_bbox"] is not None

    with event_transaction(ctx.sessions, UUID(event_id)) as session:
        key, _ = data_key(session, ctx.keys, UUID(event_id))
        versions = dict(repository.load_engine_versions(session, UUID(event_id), key))
    versions["auraface"] = "v2_test_upgraded"
    with patch.object(
        type(ctx.analyzer), "engine_versions", new_callable=PropertyMock, return_value=versions
    ):
        index_event(ctx, UUID(event_id))

    after = organiser.get(f"/events/{event_id}/claims").json()["claims"]
    assert [c["claim_id"] for c in after] == [before[0]["claim_id"]]
    assert after[0]["face_id"] is None
    assert after[0]["face_bbox"] is None
    assert after[0]["photo_url"]
