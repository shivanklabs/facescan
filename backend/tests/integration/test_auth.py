"""Sign-in and sessions: passkeys, staff keys, cookies, rotation, sign-out, CSRF guard."""

import hashlib
from datetime import UTC, datetime, timedelta

from app.core.login_sessions import SESSION_COOKIE
from app.core.services_setup import AppContext
from app.features.login import service as login_service
from app.features.login.models import AuthSession, StaffKey, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from tests.helpers import (
    SoftAuthenticator,
    key_only_client,
    new_client,
    passkey_guest,
    passkey_sign_in,
    register_passkey,
)


def _guest_with_authenticator(ctx: AppContext) -> tuple[TestClient, SoftAuthenticator]:
    """Purpose: a passkey-only guest plus the authenticator that holds their passkey."""
    client = new_client(ctx)
    authenticator = SoftAuthenticator()
    options = client.post("/auth/guest/passkey/options").json()
    response = client.post(
        "/auth/guest/passkey/verify", json={"credential": authenticator.create(options)}
    )
    assert response.status_code == 200, response.text
    return client, authenticator


def test_staff_keys_are_stored_only_as_a_hash(ctx: AppContext) -> None:
    """Purpose: the database holds the SHA-256 of a staff key, never the key itself."""
    key, view = login_service.create_staff_key(ctx, None, "organiser", None)
    with ctx.sessions() as s:
        stored = s.scalar(select(StaffKey.key_hash).where(StaffKey.id == view.key_id))
    assert stored == hashlib.sha256(key.encode()).digest()
    assert stored is not None and key.encode() not in stored


def test_session_cookie_flags_and_hash_only_storage(ctx: AppContext) -> None:
    client = new_client(ctx)
    options = client.post("/auth/guest/passkey/options").json()
    response = client.post(
        "/auth/guest/passkey/verify", json={"credential": SoftAuthenticator().create(options)}
    )
    header = response.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict", "path=/"):
        assert flag in header
    token = client.cookies.get(SESSION_COOKIE)
    assert token
    with ctx.sessions() as s:
        hashes = s.scalars(select(AuthSession.token_hash)).all()
    assert hashlib.sha256(token.encode()).digest() in hashes


def test_session_rotates_at_sign_in_and_logout_ends_it(ctx: AppContext) -> None:
    client, authenticator = _guest_with_authenticator(ctx)
    old_token = client.cookies.get(SESSION_COOKIE)
    passkey_sign_in(client, authenticator)
    new_token = client.cookies.get(SESSION_COOKIE)
    assert old_token and new_token and old_token != new_token
    stale = new_client(ctx)
    stale.cookies.set(SESSION_COOKIE, str(old_token))
    assert stale.get("/auth/me").status_code == 401  # the replaced session was revoked
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").json()["sessions_ended"] == 1
    reuse = new_client(ctx)
    reuse.cookies.set(SESSION_COOKIE, str(new_token))
    assert reuse.get("/auth/me").status_code == 401


def test_sign_out_everywhere_ends_every_session(ctx: AppContext) -> None:
    phone, authenticator = _guest_with_authenticator(ctx)
    laptop = new_client(ctx)
    passkey_sign_in(laptop, authenticator)
    assert laptop.get("/auth/me").status_code == 200
    assert phone.post("/auth/logout-everywhere").json()["sessions_ended"] == 2
    assert laptop.get("/auth/me").status_code == 401
    assert phone.get("/auth/me").status_code == 401


def test_passkey_registration_and_sign_in(ctx: AppContext) -> None:
    client = key_only_client(ctx, "organiser")
    assert client.get("/auth/me").json()["auth_method"] == "magic_link"  # staff key, no passkey
    authenticator = register_passkey(client)
    me = client.get("/auth/me").json()
    assert me["auth_method"] == "passkey" and me["has_passkey"] is True
    other_device = new_client(ctx)
    passkey_sign_in(other_device, authenticator)
    assert other_device.get("/auth/me").json()["user_id"] == me["user_id"]


def test_passkey_rejects_wrong_origin_replay_and_unknown_keys(ctx: AppContext) -> None:
    client = key_only_client(ctx, "organiser")
    authenticator = SoftAuthenticator()
    options = client.post("/auth/passkey/register/options").json()
    evil = authenticator.create(options, origin="https://evil.example")
    assert (
        client.post("/auth/passkey/register/verify", json={"credential": evil}).status_code == 400
    )
    authenticator = register_passkey(client)
    signer = new_client(ctx)
    options = signer.post("/auth/passkey/login/options").json()
    assertion = authenticator.get(options)
    assert (
        signer.post("/auth/passkey/login/verify", json={"credential": assertion}).status_code == 200
    )
    replay = new_client(ctx).post("/auth/passkey/login/verify", json={"credential": assertion})
    assert replay.status_code == 400  # challenge already used
    stranger = SoftAuthenticator()
    stranger.user_handle = b"x" * 16
    options = signer.post("/auth/passkey/login/options").json()
    unknown = signer.post("/auth/passkey/login/verify", json={"credential": stranger.get(options)})
    assert unknown.status_code == 400


def test_cross_origin_state_changes_are_refused(ctx: AppContext) -> None:
    client = new_client(ctx)
    evil = client.post("/auth/guest/passkey/options", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = client.post(
        "/auth/guest/passkey/options", headers={"Origin": ctx.settings.auth_rp_origin}
    )
    assert same.status_code == 200


def test_only_people_with_an_organiser_key_create_events(ctx: AppContext) -> None:
    someone = passkey_guest(ctx)  # passkey session, but no organiser key
    assert someone.post("/events", json={"name": "Mine", "country": "IN"}).status_code == 403
    assert someone.get("/auth/me").json()["can_create_events"] is False


# ---------------------------------------------------------------- passkey-only guests


def _user_count(ctx: AppContext) -> int:
    with ctx.sessions() as session:
        return int(session.scalar(select(func.count()).select_from(User)) or 0)


def test_guest_signs_up_and_comes_back_with_only_a_passkey(ctx: AppContext) -> None:
    """Purpose: no email: a passkey alone creates the account and signs back in."""
    client = new_client(ctx)
    options = client.post("/auth/guest/passkey/options").json()
    authenticator = SoftAuthenticator()
    response = client.post(
        "/auth/guest/passkey/verify", json={"credential": authenticator.create(options)}
    )
    assert response.status_code == 200, response.text
    me = client.get("/auth/me").json()
    assert me["auth_method"] == "passkey"
    assert me["can_create_events"] is False and me["is_staff"] is False

    other_device = new_client(ctx)
    passkey_sign_in(other_device, authenticator)
    assert other_device.get("/auth/me").json()["user_id"] == me["user_id"]


def test_failed_guest_passkey_creates_no_account(ctx: AppContext) -> None:
    """Purpose: an account exists only after its passkey verifies (wrong origin here)."""
    before = _user_count(ctx)
    client = new_client(ctx)
    options = client.post("/auth/guest/passkey/options").json()
    bad = SoftAuthenticator().create(options, origin="https://evil.example")
    response = client.post("/auth/guest/passkey/verify", json={"credential": bad})
    assert response.status_code == 400
    assert _user_count(ctx) == before
    assert client.get("/auth/me").status_code == 401


# ---------------------------------------------------------------- staff keys (no email)


def _redeem(client: TestClient, key: str) -> None:
    response = client.post("/auth/staff-key", json={"key": key})
    assert response.status_code == 200, response.text


def test_terminal_key_makes_the_first_admin_once(ctx: AppContext) -> None:
    """Purpose: the bootstrap key works once, gives admin rights, and staff pages need a passkey."""
    key, _ = login_service.create_staff_key(ctx, None, "admin", None)
    admin = new_client(ctx)
    _redeem(admin, key)
    me = admin.get("/auth/me").json()
    assert me["is_admin"] is True and me["can_create_events"] is True
    assert admin.get("/admin/health").status_code == 403  # email-link strength: passkey first
    register_passkey(admin)
    assert admin.get("/admin/health").status_code == 200

    again = new_client(ctx)
    assert again.post("/auth/staff-key", json={"key": key}).status_code == 400


def test_admin_owner_photographer_keys_end_to_end(ctx: AppContext) -> None:
    """Purpose: admin -> organiser key -> organiser's event -> photographer key for that event."""
    admin = new_client(ctx)
    _redeem(admin, login_service.create_staff_key(ctx, None, "admin", None)[0])
    register_passkey(admin)

    created = admin.post("/auth/staff-keys", json={"role": "organiser"})
    assert created.status_code == 201, created.text
    organiser = new_client(ctx)
    _redeem(organiser, created.json()["key"])
    register_passkey(organiser)
    event = organiser.post("/events", json={"name": "Key Wedding", "country": "IN"})
    assert event.status_code == 201, event.text
    event_id = event.json()["event_id"]

    photo_key = organiser.post(
        "/auth/staff-keys", json={"role": "photographer", "event_id": event_id}
    )
    assert photo_key.status_code == 201, photo_key.text
    photographer = new_client(ctx)
    _redeem(photographer, photo_key.json()["key"])
    me = photographer.get("/auth/me").json()
    assert me["is_staff"] is True and me["can_create_events"] is False
    roles = {e["event_id"]: e["roles"] for e in photographer.get("/events").json()["events"]}
    assert roles[event_id] == ["photographer"]

    # The key list never shows a key or its hash.
    listed = organiser.get("/auth/staff-keys").json()["keys"]
    assert listed[0]["used"] is True and "key" not in listed[0]


def test_staff_keys_refuse_wrong_creators_revoked_and_expired(ctx: AppContext) -> None:
    """Purpose: guests cannot mint organiser keys; revoked and expired keys do not work."""
    guest = new_client(ctx)
    options = guest.post("/auth/guest/passkey/options").json()
    guest.post(
        "/auth/guest/passkey/verify", json={"credential": SoftAuthenticator().create(options)}
    )
    assert guest.post("/auth/staff-keys", json={"role": "organiser"}).status_code == 403

    admin = new_client(ctx)
    _redeem(admin, login_service.create_staff_key(ctx, None, "admin", None)[0])
    register_passkey(admin)
    made = admin.post("/auth/staff-keys", json={"role": "organiser"}).json()
    assert admin.post(f"/auth/staff-keys/{made['key_id']}/revoke").status_code == 204
    assert new_client(ctx).post("/auth/staff-key", json={"key": made["key"]}).status_code == 400

    old_key, view = login_service.create_staff_key(ctx, None, "organiser", None)
    with ctx.sessions() as session, session.begin():
        session.execute(
            update(StaffKey)
            .where(StaffKey.id == view.key_id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    assert new_client(ctx).post("/auth/staff-key", json={"key": old_key}).status_code == 400
