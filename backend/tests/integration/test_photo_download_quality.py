"""Test photo download resolution, recompression, and metadata handling."""

import hashlib
import io
from uuid import UUID

import numpy as np
from app.core.context import AppContext
from app.db.event_scope import event_transaction
from app.features.events.models import EventMember
from app.features.face_matching.models import Match
from app.features.photo_upload.models import Photo
from fastapi.testclient import TestClient
from PIL import Image, ImageCms
from sqlalchemy import select
from tests.helpers import guest_client, searchable_event


def make_jpeg_with_exif(
    person: int = 1, count: int = 1, size: tuple[int, int] = (640, 480)
) -> bytes:
    """
    Purpose: create test JPEG image with rich metadata (ICC profile, orientation,
        camera Make/Model, GPS tags, serial numbers, MakerNote) and person marker.
    Inputs: person id, face count, dimensions (w, h).
    Outputs: JPEG bytes with EXIF data and ICC profile.
    Side effects: none.
    """
    img = Image.new("RGB", size, (200, 200, 200))
    # Marker pixel for FakeFaceAnalyzer (red=person, green=count)
    img.putpixel((0, 0), (person, count, 0))

    exif = img.getexif()
    # Orientation: 6 = 90 deg CW
    exif[274] = 6
    # Camera metadata
    exif[271] = "FaceScan Test Camera"
    exif[272] = "Model-X"
    # Serial numbers
    exif[42033] = "BODY-SERIAL-12345"
    exif[42037] = "LENS-SERIAL-67890"
    # MakerNote
    exif[37500] = b"CONFIDENTIAL_MAKERNOTE"

    # GPS IFD
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (19, 4, 30)

    # Exif IFD sub-dictionary
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[42033] = "BODY-SERIAL-12345"
    exif_ifd[37500] = b"CONFIDENTIAL_MAKERNOTE"

    # ICC profile
    srgb = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(srgb).tobytes()

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif, icc_profile=icc_bytes, quality=90)
    return buf.getvalue()


def test_photo_download_resolution_and_metadata_behavior(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify the five production photo download quality invariants:
      1. Decoded pixels match uploaded photo exactly (np.array_equal).
      2. Compressed image scan data is not re-encoded (JPEG MCU data identical).
      3. ICC profile and orientation are preserved.
      4. GPS tags, serial numbers, and MakerNote are stripped losslessly.
      5. Stored original file on disk is never modified (SHA-256 unchanged).
    Inputs: organiser (TestClient), ctx (AppContext).
    Outputs: none.
    Side effects: uploads photo, releases match, downloads photo via HTTP endpoint.
    """
    target_jpeg = make_jpeg_with_exif(person=1, count=1, size=(640, 480))
    initial_sha = hashlib.sha256(target_jpeg).hexdigest()

    event_id, invite_url, photo_ids = searchable_event(ctx, organiser, [target_jpeg])
    photo_id = photo_ids[0]

    # Verify stored file matches target_jpeg exactly before download
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        photo_row = s.scalar(select(Photo).where(Photo.id == UUID(photo_id)))
        assert photo_row is not None
        bucket, obj_key = photo_row.bucket, photo_row.object_key

    stored_bytes_before = ctx.storage.get_object(bucket, UUID(event_id), obj_key)
    assert hashlib.sha256(stored_bytes_before).hexdigest() == initial_sha

    # Guest sign in, join event, and consent
    guest = guest_client(ctx, "guest@example.test", event_id, invite_url)

    # Release photo to guest gallery (simulating claim or organiser approval)
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        member = s.scalar(
            select(EventMember).where(
                EventMember.event_id == UUID(event_id), EventMember.role == "guest"
            )
        )
        assert member is not None
        s.add(
            Match(
                event_id=UUID(event_id),
                user_id=member.user_id,
                photo_id=UUID(photo_id),
                score_band="release",
                source="claim",
            )
        )

    # Download released photo
    download_resp = guest.get(f"/events/{event_id}/gallery/{photo_id}/download")
    assert download_resp.status_code == 200
    assert download_resp.headers["content-type"] == "image/jpeg"
    assert download_resp.headers["cache-control"] == "private, no-store"

    downloaded_bytes = download_resp.content

    # Invariant 1: Decoded pixels match uploaded photo exactly
    with (
        Image.open(io.BytesIO(downloaded_bytes)) as dl_img,
        Image.open(io.BytesIO(target_jpeg)) as orig_img,
    ):
        assert dl_img.size == (640, 480)
        assert np.array_equal(np.array(dl_img), np.array(orig_img))

        # Invariant 2: Compressed image scan data is not re-encoded (JPEG MCUs unchanged)
        orig_sos = target_jpeg.find(b"\xff\xda")
        dl_sos = downloaded_bytes.find(b"\xff\xda")
        assert orig_sos > 0 and dl_sos > 0
        assert target_jpeg[orig_sos:] == downloaded_bytes[dl_sos:]

        # Invariant 3: ICC profile and orientation are preserved
        assert dl_img.info.get("icc_profile") == orig_img.info.get("icc_profile")
        dl_exif = dl_img.getexif()
        assert dl_exif.get(274) == 6  # Orientation preserved
        assert dl_exif.get(271) == "FaceScan Test Camera"
        assert dl_exif.get(272) == "Model-X"

        # Invariant 4: GPS, serial numbers, and MakerNote are removed
        assert 34853 not in dl_exif
        assert len(dl_exif.get_ifd(0x8825)) == 0
        assert 42033 not in dl_exif
        assert 42037 not in dl_exif
        assert 37500 not in dl_exif
        if 0x8769 in dl_exif:
            sub_ifd = dl_exif.get_ifd(0x8769)
            assert 42033 not in sub_ifd
            assert 42037 not in sub_ifd
            assert 37500 not in sub_ifd

    # Invariant 5: Stored file on disk is never modified
    stored_bytes_after = ctx.storage.get_object(bucket, UUID(event_id), obj_key)
    assert hashlib.sha256(stored_bytes_after).hexdigest() == initial_sha
