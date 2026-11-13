"""One scan across all events and all of my photos (guests sign in with a passkey only)."""

import base64
from uuid import UUID

import pytest
from app.core.services_setup import AppContext
from app.db.event_isolation import ADMIN_SCOPE, event_transaction, scoped_transaction
from app.features.activity_log.models import AuditLog
from app.features.consent.models import Consent
from app.features.events.models import EventMember
from app.features.face_matching.models import Match, UserTemplate
from app.features.login.models import Passkey, User
from app.features.photo_upload.models import Photo
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import make_png
from tests.helpers import (
    FRAMES_HEADERS,
    SoftAuthenticator,
    frames_body,
    new_client,
    passkey_guest,
    searchable_event,
    sign_up,
    staff_client,
)

PERSON_1_A = [make_png(1, 2, (64 + i, 48)) for i in range(3)]
PERSON_2_A = [make_png(2, 2, (90 + i, 48)) for i in range(3)]
PERSON_1_B = [make_png(1, 2, (120 + i, 48)) for i in range(2)]
PERSON_3_B = [make_png(3, 2, (150 + i, 48)) for i in range(3)]


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
    a_id, _, a_photos = searchable_event(ctx, organiser, PERSON_1_A + PERSON_2_A, name="Event A")
    b_id, _, b_photos = searchable_event(ctx, organiser, PERSON_1_B + PERSON_3_B, name="Event B")
    return [a_id, b_id], a_photos[:3] + b_photos[:2]


def test_one_scan_finds_my_photos_in_every_event(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    event_ids, person_1_photos = events
    guest = passkey_guest(ctx)
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
    guest = passkey_guest(ctx)
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


def test_organiser_sees_only_guest_numbers_on_claims(
    ctx: AppContext, organiser: TestClient, events: tuple[list[str], list[str]]
) -> None:
    """
    Purpose: a guest's claim shows up as "Guest #1" for the organiser (no names, no emails).
    """
    (event_a, _), _ = events
    guest = passkey_guest(ctx)
    assert scan(guest, 3).status_code == 200  # becomes a guest of every event
    candidates = guest.post(
        f"/events/{event_a}/scene-search", json={"query": "people at the event"}
    ).json()["candidates"]
    candidate = next(c for c in candidates if c["faces"])
    wrong_face = base64.b64encode(make_png(9, 1, (40, 30))).decode()
    claim = guest.post(
        f"/events/{event_a}/scene-search/claims",
        json={
            "photo_id": candidate["photo_id"],
            "face_id": candidate["faces"][0]["face_id"],
            "frames": [wrong_face, wrong_face, wrong_face],
            "front_only": False,
        },
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["status"] == "queued_for_review"

    queue = organiser.get(f"/events/{event_a}/claims")
    assert queue.status_code == 200
    assert queue.json()["claims"][0]["claimant_email"] == "Guest #1"
    assert "@" not in queue.text


def test_me_says_who_is_staff(
    ctx: AppContext, organiser: TestClient, events: tuple[list[str], list[str]]
) -> None:
    """
    Purpose: /auth/me tells the site who may open Studio: organisers and photographers of any
        event are staff; a guest is not.
    """
    (event_a, _), _ = events
    guest = passkey_guest(ctx)
    assert guest.get("/auth/me").json()["is_staff"] is False
    assert organiser.get("/auth/me").json()["is_staff"] is True

    photographer = staff_client(ctx, "photographer", event_a)
    me = photographer.get("/auth/me").json()
    assert me["is_staff"] is True
    assert me["can_create_events"] is False


def test_person_without_photos_gets_nothing(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    guest = passkey_guest(ctx)
    body = scan(guest, 9).json()
    assert body["released"] == 0
    assert guest.get("/discover/photos").json() == {"events": []}


def test_staff_are_never_searched_in_their_own_events(
    ctx: AppContext, organiser: TestClient, events: tuple[list[str], list[str]]
) -> None:
    """Purpose: the organiser of events A and B is never searched in them."""
    body = scan(organiser, 1).json()
    assert body == {"released": 0, "events_with_photos": 0, "events_searched": 0}


def test_scan_needs_the_current_notice_and_a_sign_in(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    guest = passkey_guest(ctx)
    assert scan(guest, 1, version="old-version").status_code == 409
    anonymous = new_client(ctx)
    assert scan(anonymous, 1, version="v1").status_code == 401


# ---------------------------------------------------------------- delete my account (DPDP)


def _rows_for(ctx: AppContext, event_id: str, user_id: UUID) -> dict[str, int]:
    """Purpose: count the person's rows in one event's tables (inside that event's scope)."""
    counts: dict[str, int] = {}
    with event_transaction(ctx.sessions, UUID(event_id)) as s:
        for name, model in (
            ("matches", Match),
            ("consents", Consent),
            ("members", EventMember),
            ("templates", UserTemplate),
        ):
            counts[name] = int(
                s.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))
                or 0
            )
        counts["photos"] = int(s.scalar(select(func.count()).select_from(Photo)) or 0)
    return counts


def test_delete_my_account_erases_my_data_and_keeps_event_photos(
    ctx: AppContext, events: tuple[list[str], list[str]]
) -> None:
    """Purpose: erasure removes everything that is mine; every event photo stays."""
    event_ids, _ = events
    guest = new_client(ctx)
    authenticator = SoftAuthenticator()
    assert sign_up(guest, authenticator).status_code == 200
    assert scan(guest, 1).json()["released"] == 5
    user_id = UUID(guest.get("/auth/me").json()["user_id"])
    before = {e: _rows_for(ctx, e, user_id) for e in event_ids}
    assert all(c["matches"] > 0 and c["consents"] > 0 for c in before.values())

    assert guest.post("/auth/delete-account", json={"confirm": "delete"}).status_code == 422
    response = guest.post("/auth/delete-account", json={"confirm": "DELETE"})
    assert response.status_code == 200, response.text
    assert guest.get("/auth/me").status_code == 401

    for e in event_ids:
        after = _rows_for(ctx, e, user_id)
        assert after["matches"] == after["consents"] == after["members"] == 0
        assert after["templates"] == 0
        assert after["photos"] == before[e]["photos"]  # event photos are never deleted
    with ctx.sessions() as s:
        assert s.get(User, user_id) is None
        assert (
            s.scalar(select(func.count()).select_from(Passkey).where(Passkey.user_id == user_id))
            == 0
        )

    # The old passkey no longer signs anyone in.
    other = new_client(ctx)
    login = other.post("/auth/passkey/login/options").json()
    assert (
        other.post(
            "/auth/passkey/login/verify", json={"credential": authenticator.get(login)}
        ).status_code
        == 400
    )

    # One audit line, without the person's id or any personal data.
    with scoped_transaction(ctx.sessions, ADMIN_SCOPE) as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "auth.account_deleted")).all()
    assert len(rows) == 1
    assert str(user_id) not in rows[0].actor and str(user_id) not in str(rows[0].details)


def test_owners_cannot_delete_their_account_and_signed_out_people_cannot_either(
    ctx: AppContext, organiser: TestClient, events: tuple[list[str], list[str]]
) -> None:
    """Purpose: an owner of events is refused (hand the events over first); no session, 401."""
    refused = organiser.post("/auth/delete-account", json={"confirm": "DELETE"})
    assert refused.status_code == 409
    assert organiser.get("/auth/me").status_code == 200
    assert (
        new_client(ctx).post("/auth/delete-account", json={"confirm": "DELETE"}).status_code == 401
    )
