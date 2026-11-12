"""Milestone 7: Google sign-in, one scan across all events, and all of my photos."""

import pytest
from app.core.services_setup import AppContext
from app.interfaces.capabilities import Capabilities
from app.interfaces.identity_provider import IdTokenRejectedError, VerifiedIdentity
from fastapi.testclient import TestClient
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    ORGANISER_EMAIL,
    frames_body,
    new_client,
    searchable_event,
)

PERSON_1_A = [make_png(1, 2, (64 + i, 48)) for i in range(3)]
PERSON_2_A = [make_png(2, 2, (90 + i, 48)) for i in range(3)]
PERSON_1_B = [make_png(1, 2, (120 + i, 48)) for i in range(2)]
PERSON_3_B = [make_png(3, 2, (150 + i, 48)) for i in range(3)]


class FakeGoogle:
    """Accepts tokens of the form 'google-ok:<email>' (no network, no crypto)."""

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(adapter_name="fake-google")

    @property
    def enabled(self) -> bool:
        return True

    def verify(self, id_token: str) -> VerifiedIdentity:
        if not id_token.startswith("google-ok:"):
            raise IdTokenRejectedError("bad token")
        email = id_token.split(":", 1)[1]
        return VerifiedIdentity(subject=f"sub-{email}", email=email)


def google_client(ctx: AppContext, email: str) -> TestClient:
    client = new_client(ctx)
    response = client.post("/auth/google", json={"credential": f"google-ok:{email}"})
    assert response.status_code == 200, response.text
    return client


def selfie(person: int) -> bytes:
    return frames_body(*[make_png(person, 1, (40 + i, 30)) for i in range(3)])


def notice_version(client: TestClient) -> str:
    return str(client.get("/consent/notice").json()["version"])


def scan(client: TestClient, person: int, version: str | None = None) -> object:
    v = version or notice_version(client)
    return client.post(
        f"/discover/search?notice_version={v}", content=selfie(person), headers=FRAMES_HEADERS
    )


@pytest.fixture
def events(organiser: TestClient, ctx: AppContext) -> tuple[list[str], list[str]]:
    ctx.identity = FakeGoogle()
    a_id, _, a_photos = searchable_event(ctx, organiser, PERSON_1_A + PERSON_2_A, name="Event A")
    b_id, _, b_photos = searchable_event(ctx, organiser, PERSON_1_B + PERSON_3_B, name="Event B")
    return [a_id, b_id], a_photos[:3] + b_photos[:2]


def test_google_sign_in_rejects_bad_tokens(ctx: AppContext) -> None:
    ctx.identity = FakeGoogle()
    client = new_client(ctx)
    response = client.post("/auth/google", json={"credential": "forged-token-" + "x" * 20})
    assert response.status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_one_scan_finds_my_photos_in_every_event(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    event_ids, person_1_photos = events
    guest = google_client(ctx, "person1@example.test")
    response = scan(guest, 1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"released": 5, "events_with_photos": 2, "events_searched": 2}

    photos = guest.get("/discover/photos").json()["events"]
    assert {e["event_name"] for e in photos} == {"Event A", "Event B"}
    got = {p["photo_id"] for e in photos for p in e["photos"]}
    assert got == set(person_1_photos)
    # Original-quality download works through the existing gallery route.
    first = photos[0]["photos"][0]
    assert guest.get(first["download_path"]).status_code == 200
    # No vectors or scores anywhere in the responses.
    assert "score" not in response.text and "embedding" not in response.text


def test_a_new_scan_never_undoes_a_withdrawal(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    """
    Purpose: after a person withdraws consent for one event, scanning again searches only the
        other events and shows nothing from the withdrawn one, until they turn it back on.
    """
    (event_a, _event_b), _ = events
    guest = google_client(ctx, "person1@example.test")
    assert scan(guest, 1).json()["events_searched"] == 2

    assert guest.post(f"/events/{event_a}/consent/withdraw").status_code == 200
    again = scan(guest, 1).json()
    assert again["events_searched"] == 1
    names = {e["event_name"] for e in guest.get("/discover/photos").json()["events"]}
    assert names == {"Event B"}

    # Turning it back on (Privacy page) brings the event back.
    body = {"notice_version": notice_version(guest), "language": "en", "face_search": True}
    assert guest.post(f"/events/{event_a}/consent", json=body).status_code == 204
    assert scan(guest, 1).json()["events_searched"] == 2
    names = {e["event_name"] for e in guest.get("/discover/photos").json()["events"]}
    assert names == {"Event A", "Event B"}


def test_person_without_photos_gets_nothing(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    guest = google_client(ctx, "stranger@example.test")
    body = scan(guest, 9).json()
    assert body["released"] == 0
    assert guest.get("/discover/photos").json() == {"events": []}


def test_staff_are_never_searched_in_their_own_events(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    organiser = google_client(ctx, ORGANISER_EMAIL)
    body = scan(organiser, 1).json()
    assert body == {"released": 0, "events_with_photos": 0, "events_searched": 0}


def test_scan_needs_the_current_notice_and_a_sign_in(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    guest = google_client(ctx, "person1@example.test")
    assert scan(guest, 1, version="old-version").status_code == 409
    anonymous = new_client(ctx)
    assert scan(anonymous, 1, version="v1").status_code == 401


def test_google_session_counts_as_strong_sign_in_for_organisers(ctx: AppContext) -> None:
    ctx.identity = FakeGoogle()
    organiser = google_client(ctx, ORGANISER_EMAIL)
    created = organiser.post("/events", json={"name": "Google event", "country": "IN"})
    assert created.status_code == 201, created.text


def test_dev_login_works_only_in_dev(ctx: AppContext) -> None:
    client = new_client(ctx)
    assert client.get("/auth/google/config").json()["dev_login"] is True
    response = client.post("/auth/dev-login", json={"email": ORGANISER_EMAIL})
    assert response.status_code == 200, response.text
    assert client.get("/auth/me").json()["auth_method"] == "dev"
    created = client.post("/events", json={"name": "Dev event", "country": "IN"})
    assert created.status_code == 201, created.text

    original = ctx.settings
    try:
        ctx.settings = original.model_copy(update={"env": "test"})
        other = new_client(ctx)
        assert other.get("/auth/google/config").json()["dev_login"] is False
        refused = other.post("/auth/dev-login", json={"email": "someone@example.test"})
        assert refused.status_code == 403
    finally:
        ctx.settings = original


def test_blink_burst_of_seven_frames_is_accepted_and_eight_refused(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    guest = google_client(ctx, "burst@example.test")
    v = notice_version(guest)
    seven = frames_body(*[make_png(1, 1, (40 + i, 30)) for i in range(7)])
    ok = guest.post(f"/discover/search?notice_version={v}", content=seven, headers=FRAMES_HEADERS)
    assert ok.status_code == 200, ok.text
    assert ok.json()["released"] == 5
    eight = frames_body(*[make_png(1, 1, (40 + i, 30)) for i in range(8)])
    refused = guest.post(
        f"/discover/search?notice_version={v}", content=eight, headers=FRAMES_HEADERS
    )
    assert refused.status_code in (413, 422)


def test_look_scan_works_only_in_dev(ctx: AppContext, events: tuple[list[str], list[str]]) -> None:
    guest = google_client(ctx, "look@example.test")
    v = notice_version(guest)
    url = f"/discover/search?notice_version={v}&method=look"
    ok = guest.post(url, content=selfie(1), headers=FRAMES_HEADERS)
    assert ok.status_code == 200, ok.text
    assert (
        guest.post(f"{url[:-4]}bad", content=selfie(1), headers=FRAMES_HEADERS).status_code == 422
    )

    original = ctx.settings
    try:
        ctx.settings = original.model_copy(update={"env": "test"})
        refused = guest.post(url, content=selfie(1), headers=FRAMES_HEADERS)
        assert refused.status_code == 403
    finally:
        ctx.settings = original
