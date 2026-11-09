"""Guest flow v1 over the API: invite -> sign-in -> age -> consent -> selfie search -> gallery
-> download -> withdrawal, plus every rule around it."""

import io
import uuid
from urllib.parse import urlsplit

import pytest
from app.adapters.storage.local_filesystem_storage import LocalFilesystemStorage
from app.core.context import AppContext
from app.db.event_scope import event_transaction
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    frames_body,
    guest_client,
    magic_sign_in,
    new_client,
    searchable_event,
    staff_client,
)
from tests.integration.test_upload_and_index import assert_no_biometrics

PERSON_1 = [make_png(1, 2, (64 + i, 48)) for i in range(4)]
PERSON_2 = [make_png(2, 2, (90 + i, 48)) for i in range(3)]


def selfie_frames(person: int, count: int = 3) -> bytes:
    return frames_body(*[make_png(person, 1, (40 + i, 30)) for i in range(count)])


def search(client: TestClient, event_id: str, body: bytes) -> object:
    return client.post(f"/events/{event_id}/search", content=body, headers=FRAMES_HEADERS)


@pytest.fixture
def event(organiser: TestClient, ctx: AppContext) -> tuple[str, str, list[str]]:
    return searchable_event(ctx, organiser, PERSON_1 + PERSON_2)


def test_full_guest_flow(organiser: TestClient, ctx: AppContext) -> None:
    # Organiser adds a photographer, who uploads with a passkey session.
    event_id = organiser.post("/events", json={"name": "Wedding", "country": "IN"}).json()[
        "event_id"
    ]
    added = organiser.post(
        f"/events/{event_id}/members", json={"email": "photo@example.test", "role": "photographer"}
    )
    assert added.status_code == 204
    photographer = staff_client(ctx, "photo@example.test")
    files = [("files", (f"p{i}.png", d, "image/png")) for i, d in enumerate(PERSON_1 + PERSON_2)]
    body = photographer.post(f"/events/{event_id}/photos", files=files).json()
    assert (
        photographer.post(
            f"/events/{event_id}/collections/{body['collection_id']}/approve"
        ).status_code
        == 403
    )
    assert (
        organiser.post(
            f"/events/{event_id}/collections/{body['collection_id']}/approve"
        ).status_code
        == 200
    )
    from app.worker import run_one

    while run_one(ctx, "test-worker"):
        pass
    invite = organiser.post(f"/events/{event_id}/invites", json={"days": 7}).json()
    assert invite["url"].startswith(f"{ctx.settings.auth_rp_origin}/e/{event_id}?k=")
    key = invite["url"].split("?k=")[1]
    assert "url" not in organiser.get(f"/events/{event_id}/invites").json()["invites"][0] or (
        organiser.get(f"/events/{event_id}/invites").json()["invites"][0]["url"] is None
    )

    # Guest: landing page before sign-in, then sign-in, age, consent, search.
    guest = new_client(ctx)
    landing = guest.post(f"/invites/{event_id}/resolve", json={"key": key})
    assert landing.status_code == 200 and landing.json()["name"] == "Wedding"
    signed_in = magic_sign_in(guest, ctx, "guest@example.test", next_path=f"/e/{event_id}?k={key}")
    assert signed_in["next"] == f"/e/{event_id}?k={key}"
    assert guest.post(f"/invites/{event_id}/join", json={"key": key}).status_code == 200
    status = guest.get(f"/events/{event_id}/guest-status").json()
    assert status["roles"] == ["guest"] and not status["consent_current"]
    assert status["dev_without_liveness"] is True and status["search_enabled"] is True
    assert search(guest, event_id, selfie_frames(1)).status_code == 403  # no age, no consent
    assert guest.post("/auth/age", json={"is_adult": True}).status_code == 204
    notice = guest.get("/consent/notice").json()
    assert notice["draft"] is True and set(notice["texts"]) == {"en", "hi"}
    consent = guest.post(
        f"/events/{event_id}/consent",
        json={"notice_version": notice["version"], "language": "hi", "face_search": True},
    )
    assert consent.status_code == 204
    result = search(guest, event_id, selfie_frames(1))
    assert result.status_code == 200, result.text
    found = result.json()
    assert_no_biometrics(found)
    assert set(found) == {"release", "some_photos_missing"}
    assert set(found["release"]) == set(body["photo_ids"][:4])  # only person 1's photos

    gallery = guest.get(f"/events/{event_id}/gallery").json()
    assert_no_biometrics(gallery)
    assert {p["photo_id"] for p in gallery["photos"]} == set(found["release"])
    photo = gallery["photos"][0]
    served = guest.get(urlsplit(photo["url"]).path + "?" + urlsplit(photo["url"]).query)
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    download = guest.get(photo["download_path"])
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    with Image.open(io.BytesIO(download.content)) as img:
        assert img.format == "JPEG" and not img.getexif() and "exif" not in img.info
        assert img.size == (64, 48) or img.size[1] == 48  # full resolution

    # Withdrawal: matches deleted, sessions for this event ended, gallery empty.
    withdrawn = guest.post(f"/events/{event_id}/consent/withdraw")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["matches"] == 4 and withdrawn.json()["sessions"] >= 1
    assert guest.get(f"/events/{event_id}/gallery").status_code == 401  # signed out of the event
    again = new_client(ctx)
    magic_sign_in(again, ctx, "guest@example.test")
    assert again.get(f"/events/{event_id}/gallery").status_code == 403  # no consent any more
    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        assert s.execute(text("SELECT count(*) FROM matches")).scalar() == 0
        actions = {r[0] for r in s.execute(text("SELECT action FROM audit_log"))}
    assert {
        "invite.created",
        "invite.joined",
        "consent.granted",
        "search",
        "gallery.viewed",
        "gallery.downloaded",
        "consent.withdrawn",
    } <= actions


def test_guests_never_see_each_others_matches(
    event: tuple[str, str, list[str]], ctx: AppContext
) -> None:
    event_id, invite, photo_ids = event
    one = guest_client(ctx, "one@example.test", event_id, invite)
    two = guest_client(ctx, "two@example.test", event_id, invite)
    assert set(search(one, event_id, selfie_frames(1)).json()["release"]) == set(photo_ids[:4])
    assert set(search(two, event_id, selfie_frames(2)).json()["release"]) == set(photo_ids[4:])
    gallery_two = {p["photo_id"] for p in two.get(f"/events/{event_id}/gallery").json()["photos"]}
    assert gallery_two == set(photo_ids[4:])
    assert two.get(f"/events/{event_id}/gallery/{photo_ids[0]}/download").status_code == 404


def test_organisers_and_photographers_cannot_search(
    event: tuple[str, str, list[str]], organiser: TestClient, ctx: AppContext
) -> None:
    event_id, invite, _ = event
    assert search(organiser, event_id, selfie_frames(1)).status_code == 403
    key = invite.split("?k=")[1]
    assert organiser.post(f"/invites/{event_id}/join", json={"key": key}).status_code == 200
    organiser.post("/auth/age", json={"is_adult": True})
    refused = search(organiser, event_id, selfie_frames(1))
    assert refused.status_code == 403  # staff stay staff even with a guest role
    assert organiser.get(f"/events/{event_id}/gallery").status_code in (403, 404)


def test_search_needs_consent_and_adults_only(
    event: tuple[str, str, list[str]], ctx: AppContext
) -> None:
    event_id, invite, _ = event
    no_consent = guest_client(ctx, "noconsent@example.test", event_id, invite, consent=False)
    response = search(no_consent, event_id, selfie_frames(1))
    assert response.status_code == 403 and response.json()["detail"] == "consent_required"
    minor = guest_client(ctx, "minor@example.test", event_id, invite, adult=False)
    response = search(minor, event_id, selfie_frames(1))
    assert (
        response.status_code == 403
        and response.json()["detail"] == "minor_guardian_consent_required"
    )
    notice = minor.get("/consent/notice").json()
    consent = minor.post(
        f"/events/{event_id}/consent",
        json={"notice_version": notice["version"], "language": "en", "face_search": True},
    )
    assert consent.status_code == 403
    assert minor.get(f"/events/{event_id}/guest-status").json()["is_minor"] is True


def test_search_limits_are_enforced_per_user_and_per_event(
    event: tuple[str, str, list[str]], ctx: AppContext
) -> None:
    event_id, invite, _ = event
    ctx.settings.search_per_user_per_hour = 2
    guest = guest_client(ctx, "limited@example.test", event_id, invite)
    assert search(guest, event_id, selfie_frames(1)).status_code == 200
    assert search(guest, event_id, selfie_frames(1)).status_code == 200
    assert search(guest, event_id, selfie_frames(1)).status_code == 429
    ctx.settings.search_per_user_per_hour = 100
    ctx.settings.search_per_event_per_hour = 2  # 2 attempts recorded (refused ones do not count)
    other = guest_client(ctx, "other@example.test", event_id, invite)
    assert search(other, event_id, selfie_frames(2)).status_code == 429


def test_frames_rules(event: tuple[str, str, list[str]], ctx: AppContext) -> None:
    event_id, invite, _ = event
    guest = guest_client(ctx, "frames@example.test", event_id, invite)
    assert search(guest, event_id, selfie_frames(1, count=2)).status_code == 422
    assert search(guest, event_id, selfie_frames(1, count=6)).status_code == 422
    two_faces = frames_body(make_png(1, 1), make_png(1, 2), make_png(1, 1))
    response = search(guest, event_id, two_faces)
    assert response.status_code == 422 and "frame 2" in response.json()["detail"]
    assert "found 2" in response.json()["detail"]
    wrong_type = guest.post(
        f"/events/{event_id}/search", content=b"x", headers={"Content-Type": "image/png"}
    )
    assert wrong_type.status_code == 422
    disagree = frames_body(make_png(1, 1), make_png(2, 1, (41, 30)), make_png(1, 1, (42, 30)))
    result = search(guest, event_id, disagree).json()
    assert result == {"release": [], "some_photos_missing": True}


def test_signed_gallery_urls_reject_tampering_and_expiry(
    event: tuple[str, str, list[str]], ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id, invite, _ = event
    guest = guest_client(ctx, "urls@example.test", event_id, invite)
    search(guest, event_id, selfie_frames(1))
    url = guest.get(f"/events/{event_id}/gallery").json()["photos"][0]["url"]
    parts = urlsplit(url)
    path = f"{parts.path}?{parts.query}"
    assert new_client(ctx).get(path).status_code == 200  # the URL itself is the capability
    tampered = path[:-3] + ("AAA" if not path.endswith("AAA") else "BBB")
    assert new_client(ctx).get(tampered).status_code == 403
    later = LocalFilesystemStorage._now() + ctx.settings.storage_url_expiry_minutes * 60 + 5
    monkeypatch.setattr(LocalFilesystemStorage, "_now", staticmethod(lambda: later))
    assert new_client(ctx).get(path).status_code == 403


def test_guest_search_is_disabled_outside_dev_without_liveness(
    event: tuple[str, str, list[str]], ctx: AppContext
) -> None:
    event_id, invite, _ = event
    guest = guest_client(ctx, "prod@example.test", event_id, invite)
    ctx.settings.env = "test"
    try:
        response = search(guest, event_id, selfie_frames(1))
        assert response.status_code == 403
        assert response.json()["detail"] == "search_disabled_without_liveness"
        status = guest.get(f"/events/{event_id}/guest-status").json()
        assert status["search_enabled"] is False and status["dev_without_liveness"] is False
    finally:
        ctx.settings.env = "dev"


def test_revoked_and_expired_invites_stop_working(
    event: tuple[str, str, list[str]], organiser: TestClient, ctx: AppContext
) -> None:
    event_id, invite, _ = event
    key = invite.split("?k=")[1]
    guest = new_client(ctx)
    assert guest.post(f"/invites/{event_id}/resolve", json={"key": key}).status_code == 200
    invite_id = organiser.get(f"/events/{event_id}/invites").json()["invites"][0]["invite_id"]
    assert organiser.post(f"/events/{event_id}/invites/{invite_id}/revoke").status_code == 204
    assert guest.post(f"/invites/{event_id}/resolve", json={"key": key}).status_code == 404
    magic_sign_in(guest, ctx, "late-guest@example.test")
    assert guest.post(f"/invites/{event_id}/join", json={"key": key}).status_code == 404
    fresh = (
        organiser.post(f"/events/{event_id}/invites", json={"days": 1})
        .json()["url"]
        .split("?k=")[1]
    )
    with event_transaction(ctx.sessions, uuid.UUID(event_id)) as s:
        s.execute(text("UPDATE event_invites SET expires_at = now() - interval '1 minute'"))
    assert guest.post(f"/invites/{event_id}/join", json={"key": fresh}).status_code == 404
    assert guest.post(f"/invites/{event_id}/resolve", json={"key": "x" * 40}).status_code == 404
