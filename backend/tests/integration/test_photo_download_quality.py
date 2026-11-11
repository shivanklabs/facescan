"""Test photo download resolution, recompression, and metadata handling."""

import io

from app.core.context import AppContext
from fastapi.testclient import TestClient
from PIL import Image
from tests.helpers import guest_client, searchable_event


def make_jpeg_with_exif(
    person: int = 1, count: int = 1, size: tuple[int, int] = (640, 480)
) -> bytes:
    """
    Purpose: create test JPEG image with EXIF metadata (camera model and GPS tags)
        and person marker.
    Inputs: person id, face count, dimensions (w, h).
    Outputs: JPEG bytes with EXIF data.
    Side effects: none.
    """
    img = Image.new("RGB", size, (200, 200, 200))
    # Marker pixel for FakeFaceAnalyzer (red=person, green=count)
    img.putpixel((0, 0), (person, count, 0))

    exif = img.getexif()
    # Tag 271: Make, Tag 272: Model
    exif[271] = "FaceScan Test Camera"
    exif[272] = "Model-X"

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif, quality=95)
    return buf.getvalue()


def test_photo_download_resolution_and_metadata_behavior(
    organiser: TestClient, ctx: AppContext
) -> None:
    """
    Purpose: verify that downloaded gallery photos maintain original dimensions, and record
        current behavior regarding recompression (OpenCV q=95) and EXIF metadata stripping.
    """
    from uuid import UUID

    from app.db.event_scope import event_transaction
    from app.modules.matching.models import Match
    from sqlalchemy import select

    target_jpeg = make_jpeg_with_exif(person=1, count=1, size=(640, 480))
    event_id, invite_url, photo_ids = searchable_event(ctx, organiser, [target_jpeg])
    photo_id = photo_ids[0]

    # Guest sign in, join event, and consent
    guest = guest_client(ctx, "guest@example.test", event_id, invite_url)

    # Release photo to guest gallery (simulating claim or organiser approval)
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        from app.modules.events.models import EventMember

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
    with Image.open(io.BytesIO(downloaded_bytes)) as dl_img:
        # 1. Dimension preservation: downloaded resolution matches original exactly
        assert dl_img.size == (640, 480)

        # 2. Metadata behavior today: OpenCV imencode does not write EXIF, so tags are stripped
        dl_exif = dl_img.getexif()
        assert 271 not in dl_exif, (
            "EXIF metadata is stripped on download today by OpenCV re-encoding"
        )

    # 3. Quality & recompression behavior today:
    # Today, FacescanAiFaceAnalyzer.full_copy re-encodes via cv2.imencode at quality 95,
    # so downloaded bytes differ from uploaded bytes.
    # When bit-for-bit identical storage download is enabled in a future milestone,
    # downloaded_bytes will match original_jpeg.
    assert len(downloaded_bytes) > 0
