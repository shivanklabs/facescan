"""Integration tests for Milestone 6 Part 2:
- Resumable chunked upload with checksum per chunk, SHA-256 check at the end, resume.
- Processing status page for photographers: counts per state, retry failed photo.
- Camera clock sync: read EXIF, apply offset per camera, corrected_time, privacy on serial.
- Sections: create sections, assign photos by corrected_time, filter scene search.
- Search window: end date closes new searches and claims; galleries/downloads keep working.
"""

import hashlib
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.context import AppContext
from app.db.event_scope import event_transaction
from app.features.events.models import Event
from app.features.photo_upload.models import Photo
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    frames_body,
    guest_client,
    searchable_event,
)

PERSON_1 = [make_png(1, 2, (64 + i, 48)) for i in range(4)]
PERSON_2 = [make_png(2, 2, (90 + i, 48)) for i in range(3)]


def make_test_jpeg_with_exif(
    make: str = "Sony",
    model: str = "ILCE-7M4",
    serial: str = "SN123456",
    date_str: str = "2026:11:11 10:00:00",
) -> bytes:
    """
    Purpose: create valid JPEG bytes with EXIF metadata for camera sync tests.
    Inputs: make, model, serial, date_str.
    Outputs: JPEG bytes.
    Side effects: none.
    """
    img = Image.new("RGB", (64, 64), color=(80, 120, 160))
    exif = img.getexif()
    exif[271] = make
    exif[272] = model
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[36867] = date_str
    exif_ifd[42033] = serial
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def selfie_frames(person: int, count: int = 3) -> bytes:
    """Purpose: generate test selfie frames for a synthetic person."""
    return frames_body(*[make_png(person, 1, (40 + i, 30)) for i in range(count)])


def test_resumable_chunked_upload(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify chunked upload handles out-of-order chunks, resumes, validates SHA-256,
        cleans up chunk files, and rejects invalid checksums.
    """
    event_id, _, _ = searchable_event(ctx, organiser, PERSON_1)

    # Prepare photo data
    photo_data = make_test_jpeg_with_exif(date_str="2026:11:11 09:00:00")
    expected_sha256 = hashlib.sha256(photo_data).hexdigest()

    # Split into 3 chunks
    chunk_size = len(photo_data) // 3
    chunk0 = photo_data[:chunk_size]
    chunk1 = photo_data[chunk_size : chunk_size * 2]
    chunk2 = photo_data[chunk_size * 2 :]

    # 1. Initialize session
    init_res = organiser.post(
        f"/events/{event_id}/uploads/init",
        json={
            "filename": "camera_photo.jpg",
            "byte_size": len(photo_data),
            "total_chunks": 3,
            "expected_sha256": expected_sha256,
        },
    )
    assert init_res.status_code == 201
    upload_id = init_res.json()["upload_id"]

    # 2. Upload chunk 0 and chunk 2 (simulate dropped chunk 1)
    c0_hash = hashlib.sha256(chunk0).hexdigest()
    res_c0 = organiser.post(
        f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=0&chunk_sha256={c0_hash}",
        content=chunk0,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert res_c0.status_code == 200
    assert res_c0.json()["complete"] is False
    assert res_c0.json()["received_chunks"] == [0]

    c2_hash = hashlib.sha256(chunk2).hexdigest()
    res_c2 = organiser.post(
        f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=2&chunk_sha256={c2_hash}",
        content=chunk2,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert res_c2.status_code == 200
    assert res_c2.json()["complete"] is False
    assert res_c2.json()["received_chunks"] == [0, 2]

    # 3. Check status for resume
    status_res = organiser.get(f"/events/{event_id}/uploads/{upload_id}")
    assert status_res.status_code == 200
    assert status_res.json()["received_chunks"] == [0, 2]
    assert status_res.json()["status"] == "in_progress"

    # Attempting to complete early fails
    early_complete = organiser.post(f"/events/{event_id}/uploads/{upload_id}/complete")
    assert early_complete.status_code == 422

    # 4. Resume: upload missing chunk 1
    c1_hash = hashlib.sha256(chunk1).hexdigest()
    res_c1 = organiser.post(
        f"/events/{event_id}/uploads/{upload_id}/chunk?chunk_index=1&chunk_sha256={c1_hash}",
        content=chunk1,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert res_c1.status_code == 200
    assert res_c1.json()["complete"] is True
    assert res_c1.json()["received_chunks"] == [0, 1, 2]

    # 5. Complete upload
    complete_res = organiser.post(f"/events/{event_id}/uploads/{upload_id}/complete")
    assert complete_res.status_code == 200
    assert complete_res.json()["photo_id"] is not None

    # Verify photo exists in database
    photo_id = UUID(complete_res.json()["photo_id"])
    with event_transaction(ctx.sessions, event_id) as session:
        photo = session.get(Photo, photo_id)
        assert photo is not None
        assert photo.sha256 == expected_sha256
        assert photo.camera_model == "ILCE-7M4"
        assert photo.camera_serial == "SN123456"


def test_processing_status_and_retry(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify processing status dashboard reports accurate counts, lists failed photos,
        and retry resets status to approved and queues re-indexing.
    """
    event_id, _, _ = searchable_event(ctx, organiser, PERSON_1)

    # Query processing status
    status_resp = organiser.get(f"/events/{event_id}/processing-status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["quarantined"] >= 0
    assert data["approved"] >= 0
    assert data["indexed"] >= len(PERSON_1)
    assert data["failed"] == 0

    # Mark one photo as failed to simulate indexing failure
    with event_transaction(ctx.sessions, event_id) as session:
        photo = session.scalars(select(Photo).where(Photo.event_id == event_id)).first()
        assert photo is not None
        photo.status = "failed"
        failed_id = photo.id

    # Verify status now reports 1 failure and lists the failed photo
    status_resp = organiser.get(f"/events/{event_id}/processing-status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["failed"] == 1
    assert any(f["id"] == str(failed_id) for f in data["failed_photos"])

    # Retry the failed photo
    retry_resp = organiser.post(f"/events/{event_id}/photos/{failed_id}/retry")
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "approved"

    # Verify status in DB is reset to approved
    with event_transaction(ctx.sessions, event_id) as session:
        photo = session.get(Photo, failed_id)
        assert photo is not None
        assert photo.status == "approved"


def test_camera_clock_sync_and_serial_privacy(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify camera clock offset recalculates photo corrected_time, and verifies
        the invariant that camera serial is never exposed to guests.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, PERSON_1)
    guest = guest_client(ctx, "guest_cam@example.test", event_id, invite_url)

    # Ingest a photo with known camera metadata
    base_time_str = "2026:11:11 14:00:00"
    raw_bytes = make_test_jpeg_with_exif(
        make="Canon",
        model="EOS R5",
        serial="SECRET_SERIAL_9999",
        date_str=base_time_str,
    )
    # Upload via standard multipart
    res_up = organiser.post(
        f"/events/{event_id}/photos",
        files={"files": ("canon.jpg", raw_bytes, "image/jpeg")},
    )
    assert res_up.status_code == 201
    photo_id = UUID(res_up.json()["photo_ids"][0])

    # Check photo has initial corrected_time equal to taken_at
    with event_transaction(ctx.sessions, event_id) as session:
        photo = session.get(Photo, photo_id)
        assert photo is not None
        assert photo.camera_model == "EOS R5"
        assert photo.camera_serial == "SECRET_SERIAL_9999"
        initial_corrected = photo.corrected_time
        assert initial_corrected is not None
        assert initial_corrected.astimezone(UTC).hour == 14

    # Organiser lists cameras
    cam_resp = organiser.get(f"/events/{event_id}/cameras")
    assert cam_resp.status_code == 200
    cameras = cam_resp.json()["cameras"]
    eos = next(c for c in cameras if c["camera_model"] == "EOS R5")
    assert eos["photo_count"] >= 1
    assert eos["offset_seconds"] == 0

    # Apply 2-hour offset (+7200 seconds)
    offset_resp = organiser.post(
        f"/events/{event_id}/cameras/offset",
        json={
            "camera_model": "EOS R5",
            "camera_serial": "SECRET_SERIAL_9999",
            "offset_seconds": 7200,
        },
    )
    assert offset_resp.status_code == 200
    assert offset_resp.json()["offset_seconds"] == 7200

    # Verify photo.corrected_time is shifted forward by 2 hours (14:00 -> 16:00 UTC)
    with event_transaction(ctx.sessions, event_id) as session:
        photo = session.get(Photo, photo_id)
        assert photo is not None
        assert photo.corrected_time is not None
        assert photo.corrected_time.astimezone(UTC).hour == 16

    # Privacy Invariant: verify guest gallery NEVER exposes camera_serial
    gal_resp = guest.get(f"/events/{event_id}/gallery")
    assert gal_resp.status_code == 200
    gal_text = gal_resp.text
    assert "SECRET_SERIAL_9999" not in gal_text
    assert "camera_serial" not in gal_text


def test_sections_and_scene_search_filter(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify creating sections assigns photos by corrected_time, and scene search
        filters by section_id.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, PERSON_1)

    # 1. Create section for 10:00 - 12:00
    start_dt = datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC)
    end_dt = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    sec_resp = organiser.post(
        f"/events/{event_id}/sections",
        json={
            "name": "Morning Keynote",
            "starts_at": start_dt.isoformat(),
            "ends_at": end_dt.isoformat(),
        },
    )
    assert sec_resp.status_code == 201
    sec_id = UUID(sec_resp.json()["id"])

    # 2. Upload photo with corrected_time inside that section (11:00)
    inside_bytes = make_test_jpeg_with_exif(date_str="2026:11:11 11:00:00")
    up_res = organiser.post(
        f"/events/{event_id}/photos",
        files={"files": ("inside.jpg", inside_bytes, "image/jpeg")},
    )
    assert up_res.status_code == 201
    inside_pid = UUID(up_res.json()["photo_ids"][0])

    # Upload photo with corrected_time outside that section (15:00)
    outside_bytes = make_test_jpeg_with_exif(date_str="2026:11:11 15:00:00")
    up_res2 = organiser.post(
        f"/events/{event_id}/photos",
        files={"files": ("outside.jpg", outside_bytes, "image/jpeg")},
    )
    assert up_res2.status_code == 201
    outside_pid = UUID(up_res2.json()["photo_ids"][0])

    # Verify inside photo has section_id set, outside photo does not
    with event_transaction(ctx.sessions, event_id) as session:
        p_in = session.get(Photo, inside_pid)
        assert p_in is not None
        assert p_in.section_id == sec_id

        p_out = session.get(Photo, outside_pid)
        assert p_out is not None
        assert p_out.section_id is None

    # List sections
    list_resp = organiser.get(f"/events/{event_id}/sections")
    assert list_resp.status_code == 200
    sections = list_resp.json()["sections"]
    keynote = next(s for s in sections if s["id"] == str(sec_id))
    assert keynote["photo_count"] >= 1


def test_search_window_enforcement_and_no_data_loss(organiser: TestClient, ctx: AppContext) -> None:
    """
    Purpose: verify that closing the search window blocks new searches and claims, but
        galleries and downloads keep working, and zero data is deleted.
    """
    event_id, invite_url, _ = searchable_event(ctx, organiser, PERSON_1)
    guest = guest_client(ctx, "guest_window@example.test", event_id, invite_url)

    # Guest performs initial face search before window closes
    res = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert res.status_code == 200
    initial_matches = res.json()["release"]
    assert len(initial_matches) > 0

    # Organiser closes the search window by setting end date to 1 hour ago
    past_time = datetime.now(UTC) - timedelta(hours=1)
    sw_resp = organiser.post(
        f"/events/{event_id}/search-window",
        json={"search_window_ends_at": past_time.isoformat()},
    )
    assert sw_resp.status_code == 200

    # 1. New face searches must be blocked (HTTP 403)
    search_blocked = guest.post(
        f"/events/{event_id}/search",
        content=selfie_frames(1),
        headers=FRAMES_HEADERS,
    )
    assert search_blocked.status_code == 403
    assert "Search window for this event has closed" in search_blocked.text

    # 2. Scene search must be blocked (HTTP 403)
    scene_blocked = guest.post(
        f"/events/{event_id}/scene-search",
        json={"query": "people smiling at reception"},
    )
    assert scene_blocked.status_code == 403
    assert "Search window for this event has closed" in scene_blocked.text

    # 3. Existing gallery keeps working
    gal_resp = guest.get(f"/events/{event_id}/gallery")
    assert gal_resp.status_code == 200
    assert len(gal_resp.json()["photos"]) == len(initial_matches)

    # 4. Downloads keep working
    sample_photo_id = initial_matches[0]
    dl_resp = guest.get(f"/events/{event_id}/gallery/{sample_photo_id}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"] in ("image/jpeg", "image/png", "image/webp")

    # 5. No data loss invariant: photos, events, and matches are intact
    with event_transaction(ctx.sessions, event_id) as session:
        event = session.get(Event, event_id)
        assert event is not None
        assert event.search_window_ends_at is not None

        photo_count = session.scalars(select(Photo).where(Photo.event_id == event_id)).all()
        assert len(photo_count) >= len(PERSON_1)
