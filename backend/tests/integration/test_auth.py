"""Sign-in and sessions: passkeys, staff keys, cookies, rotation, sign-out, CSRF guard."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from app.core.login_sessions import SESSION_COOKIE
from app.core.services_setup import AppContext
from app.features.login import service as login_service
from app.features.login.models import AuthSession, StaffKey, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from tests.conftest import make_png
from tests.helpers import (
    SoftAuthenticator,
    key_only_client,
    new_client,
    passkey_guest,
    passkey_sign_in,
    register_passkey,
    sign_up,
    staff_client,
)


def _guest_with_authenticator(ctx: AppContext) -> tuple[TestClient, SoftAuthenticator]:
    """Purpose: a passkey-only guest plus the authenticator that holds their passkey."""
    client = new_client(ctx)
    authenticator = SoftAuthenticator()
    response = sign_up(client, authenticator)
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
    response = sign_up(client)
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
    body = {"name": "A", "email": "origin@example.test", "account_type": "guest"}
    evil = client.post(
        "/auth/register/options", json=body, headers={"Origin": "https://evil.example"}
    )
    assert evil.status_code == 403
    same = client.post(
        "/auth/register/options", json=body, headers={"Origin": ctx.settings.auth_rp_origin}
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
    """Purpose: name + email + passkey create the account; the passkey alone signs back in."""
    client = new_client(ctx)
    authenticator = SoftAuthenticator()
    response = sign_up(client, authenticator)
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
    response = sign_up(client, origin="https://evil.example")
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
    assert sign_up(guest).status_code == 200
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


# ---------------------------------------------------------------- accounts, roles and approvals
# One common login (passkey); the server decides role and status. Organisers and
# photographers register themselves and wait for an admin; admins come only from the
# server-side bootstrap key.


def _admin(ctx: AppContext) -> TestClient:
    """Purpose: an active admin with a passkey session (bootstrap key, then a passkey)."""
    return staff_client(ctx, "admin")


def _registered(
    ctx: AppContext, account_type: str
) -> tuple[TestClient, SoftAuthenticator, str, str]:
    """Purpose: a self-registered account. Outputs: client, authenticator, email, user id."""
    client = new_client(ctx)
    authenticator = SoftAuthenticator()
    email = f"{account_type}-{secrets.token_hex(4)}@example.test"
    response = sign_up(client, authenticator, account_type, email, f"Test {account_type}")
    assert response.status_code == 200, response.text
    return client, authenticator, email, client.get("/auth/me").json()["user_id"]


def _set_status(admin: TestClient, user_id: str, status: str) -> None:
    response = admin.post(f"/admin/accounts/{user_id}/status", json={"status": status})
    assert response.status_code == 200, response.text


def _approved(ctx: AppContext, admin: TestClient, account_type: str) -> tuple[TestClient, str]:
    """Purpose: a self-registered organiser / photographer the admin approved."""
    client, _, email, user_id = _registered(ctx, account_type)
    _set_status(admin, user_id, "active")
    return client, email


def _login(client: TestClient, authenticator: SoftAuthenticator) -> str:
    """Purpose: passkey sign-in; returns where the server sends this person."""
    options = client.post("/auth/passkey/login/options").json()
    response = client.post(
        "/auth/passkey/login/verify", json={"credential": authenticator.get(options)}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["next"])


def _upload(client: TestClient, event_id: str) -> int:
    files = [("files", ("p.png", make_png(1, 1), "image/png"))]
    return client.post(f"/events/{event_id}/photos", files=files).status_code


def test_guest_registration_is_active_at_once(ctx: AppContext) -> None:
    client = new_client(ctx)
    response = sign_up(client, account_type="guest", email="guest.reg@example.test", name="Asha")
    assert response.status_code == 200 and response.json()["next"] == "/photos"
    me = client.get("/auth/me").json()
    assert (me["role"], me["status"], me["display_name"]) == ("guest", "active", "Asha")
    with ctx.sessions() as s:
        row = s.scalars(select(User).where(User.id == me["user_id"])).one()
        assert b"guest.reg@example.test" not in row.email_encrypted  # stored encrypted only
    again = sign_up(new_client(ctx), account_type="guest", email="GUEST.reg@example.test")
    assert again.status_code == 409 and again.json()["detail"] == "email_taken"


def test_organiser_registration_waits_for_approval(ctx: AppContext) -> None:
    client, _, _, _ = _registered(ctx, "organiser")
    me = client.get("/auth/me").json()
    assert (me["role"], me["status"], me["home"]) == ("organiser", "pending", "/photos")
    assert me["can_create_events"] is False


def test_photographer_registration_waits_for_approval(ctx: AppContext) -> None:
    client, _, _, _ = _registered(ctx, "photographer")
    me = client.get("/auth/me").json()
    assert (me["role"], me["status"]) == ("photographer", "pending")
    assert me["is_staff"] is False


def test_admin_bootstrap_key_makes_an_active_admin(ctx: AppContext) -> None:
    me = _admin(ctx).get("/auth/me").json()
    assert (me["role"], me["status"], me["home"], me["is_admin"]) == (
        "admin",
        "active",
        "/admin",
        True,
    )


def test_admin_bootstrap_refuses_a_second_admin_unless_forced(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import services_setup

    _admin(ctx)  # an active admin exists now
    monkeypatch.setattr(services_setup, "build_context", lambda _settings: ctx)
    with pytest.raises(login_service.AdminExistsError):
        login_service.issue_staff_key_from_terminal("admin")
    assert login_service.issue_staff_key_from_terminal("admin", force=True).startswith("FS-")
    assert login_service.issue_staff_key_from_terminal("organiser").startswith("FS-")


def test_public_registration_can_never_make_an_admin(ctx: AppContext) -> None:
    client = new_client(ctx)
    body = {"name": "Eve", "email": "eve@example.test", "account_type": "admin"}
    assert client.post("/auth/register/options", json=body).status_code == 422
    sneaky = {"name": "Eve", "email": "eve@example.test", "account_type": "guest", "role": "admin"}
    assert client.post("/auth/register/options", json=sneaky).status_code == 422
    # Even a hand-made final request is refused before any account exists.
    assert client.post("/auth/register", json={**body, "credential": {}}).status_code == 422
    with ctx.sessions() as s:
        named_admins = s.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.display_name.is_not(None))
        )
    assert named_admins == 0  # every admin came from a bootstrap key, none from registration


def test_one_login_sends_each_person_to_their_dashboard(ctx: AppContext) -> None:
    admin = _admin(ctx)
    _, authenticator, _, user_id = _registered(ctx, "organiser")
    elsewhere = new_client(ctx)
    assert _login(elsewhere, authenticator) == "/photos"  # still waiting for approval
    _set_status(admin, user_id, "active")
    assert _login(elsewhere, authenticator) == "/studio"
    _, guest_key, _, _ = _registered(ctx, "guest")
    assert _login(new_client(ctx), guest_key) == "/photos"


def test_pending_organiser_cannot_create_events(ctx: AppContext) -> None:
    client, _, _, _ = _registered(ctx, "organiser")
    refused = client.post("/events", json={"name": "Too early", "country": "IN"})
    assert refused.status_code == 403 and refused.json()["detail"] == "account_pending"


def test_pending_photographer_cannot_be_assigned_or_upload(ctx: AppContext) -> None:
    admin = _admin(ctx)
    organiser, _ = _approved(ctx, admin, "organiser")
    event_id = organiser.post("/events", json={"name": "Mehndi", "country": "IN"}).json()[
        "event_id"
    ]
    photographer, _, email, _ = _registered(ctx, "photographer")
    refused = organiser.post(f"/events/{event_id}/photographers", json={"email": email})
    assert refused.status_code == 404 and refused.json()["detail"] == "no_approved_photographer"
    assert _upload(photographer, event_id) == 404


def test_organiser_creates_and_lists_several_events(ctx: AppContext) -> None:
    admin = _admin(ctx)
    organiser, _ = _approved(ctx, admin, "organiser")
    made = {
        organiser.post("/events", json={"name": name, "country": "IN"}).json()["event_id"]
        for name in ("Haldi", "Sangeet", "Wedding")
    }
    listed = {e["event_id"] for e in organiser.get("/events").json()["events"]}
    assert made <= listed


def test_organiser_cannot_open_another_organisers_event(ctx: AppContext) -> None:
    admin = _admin(ctx)
    owner, _ = _approved(ctx, admin, "organiser")
    other, _ = _approved(ctx, admin, "organiser")
    event_id = owner.post("/events", json={"name": "Private", "country": "IN"}).json()["event_id"]
    assert other.get(f"/events/{event_id}").status_code == 404  # existence not revealed
    assert other.get(f"/events/{event_id}/invites").status_code == 404
    assert other.get(f"/events/{event_id}/photographers").status_code == 404
    assign = other.post(f"/events/{event_id}/photographers", json={"email": "x@example.test"})
    assert assign.status_code == 404


def test_photographer_cannot_reach_an_unassigned_event(ctx: AppContext) -> None:
    admin = _admin(ctx)
    organiser, _ = _approved(ctx, admin, "organiser")
    photographer, _ = _approved(ctx, admin, "photographer")
    event_id = organiser.post("/events", json={"name": "Not yours", "country": "IN"}).json()[
        "event_id"
    ]
    assert _upload(photographer, event_id) == 404
    assert photographer.get(f"/events/{event_id}/processing-status").status_code == 404


def test_photographer_uploads_to_an_assigned_event(ctx: AppContext) -> None:
    admin = _admin(ctx)
    organiser, _ = _approved(ctx, admin, "organiser")
    photographer, email = _approved(ctx, admin, "photographer")
    event_id = organiser.post("/events", json={"name": "Assigned", "country": "IN"}).json()[
        "event_id"
    ]
    assigned = organiser.post(f"/events/{event_id}/photographers", json={"email": email})
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["display_name"] == "Test photographer"
    assert "email" not in assigned.json()
    listed = organiser.get(f"/events/{event_id}/photographers").json()["photographers"]
    assert [p["user_id"] for p in listed] == [assigned.json()["user_id"]]
    assert _upload(photographer, event_id) in (200, 201)
    # Assigning is the organiser's job only: a photographer cannot assign anyone.
    again = photographer.post(f"/events/{event_id}/photographers", json={"email": email})
    assert again.status_code == 403


def test_guests_cannot_use_staff_or_admin_apis(ctx: AppContext) -> None:
    guest = passkey_guest(ctx)
    assert guest.post("/events", json={"name": "Mine", "country": "IN"}).status_code == 403
    assert guest.get("/admin/accounts").status_code == 403
    assert guest.get("/admin/health").status_code == 403
    assert guest.post("/auth/staff-keys", json={"role": "organiser"}).status_code == 403


def test_admin_dashboard_needs_an_admin(ctx: AppContext) -> None:
    assert new_client(ctx).get("/admin/health").status_code == 401
    assert new_client(ctx).get("/admin/accounts").status_code == 401
    admin = _admin(ctx)
    organiser, _ = _approved(ctx, admin, "organiser")
    refused = organiser.get("/admin/accounts")
    assert refused.status_code == 403 and refused.json()["detail"] == "admin_required"
    assert admin.get("/admin/health").status_code == 200


def test_admin_approves_rejects_and_suspends_accounts(ctx: AppContext) -> None:
    admin = _admin(ctx)
    organiser, _, org_email, org_id = _registered(ctx, "organiser")
    photographer, _, _, ph_id = _registered(ctx, "photographer")
    pending = {a["user_id"]: a for a in admin.get("/admin/accounts").json()["accounts"]}
    assert pending[org_id]["email"] == org_email and pending[org_id]["status"] == "pending"
    assert ph_id in pending

    _set_status(admin, org_id, "active")
    created = organiser.post("/events", json={"name": "Approved", "country": "IN"})
    assert created.status_code in (200, 201)
    _set_status(admin, ph_id, "rejected")
    assert photographer.get("/auth/me").json()["status"] == "rejected"

    _set_status(admin, org_id, "suspended")
    assert organiser.get("/events").status_code in (401, 403)  # sessions ended, account locked
    # The admin's own account and other admins cannot be changed through the API.
    admin_id = admin.get("/auth/me").json()["user_id"]
    own = admin.post(f"/admin/accounts/{admin_id}/status", json={"status": "suspended"})
    assert own.status_code == 403
    bad = admin.post(f"/admin/accounts/{org_id}/status", json={"status": "admin"})
    assert bad.status_code == 422


def test_suspended_people_can_still_see_why_and_delete_their_account(ctx: AppContext) -> None:
    admin = _admin(ctx)
    _, authenticator, _, user_id = _registered(ctx, "guest")
    _set_status(admin, user_id, "suspended")
    again = new_client(ctx)
    _login(again, authenticator)
    assert again.get("/auth/me").json()["status"] == "suspended"
    refused = again.get("/discover/photos")
    assert refused.status_code == 403 and refused.json()["detail"] == "account_suspended"
    assert again.post("/auth/delete-account", json={"confirm": "DELETE"}).status_code == 200


def test_new_passkeys_ask_for_the_device_unlock_first(ctx: AppContext) -> None:
    """Purpose: browsers offer built-in fingerprint / face unlock (then a phone), not a USB key."""
    body = {"name": "Hint", "email": "hint@example.test", "account_type": "guest"}
    options = new_client(ctx).post("/auth/register/options", json=body).json()
    assert options["hints"] == ["client-device", "hybrid"]
    assert options["authenticatorSelection"]["userVerification"] == "required"
