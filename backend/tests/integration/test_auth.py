"""Sign-in and sessions: magic links, passkeys, cookies, rotation, sign-out, CSRF guard."""

import hashlib
from datetime import UTC, datetime, timedelta

from app.core.auth import SESSION_COOKIE
from app.core.context import AppContext
from app.modules.identity.models import AuthSession, MagicLink
from sqlalchemy import select, update
from tests.helpers import (
    MemoryEmailSender,
    SoftAuthenticator,
    magic_sign_in,
    new_client,
    passkey_sign_in,
    register_passkey,
    staff_client,
)


def email(ctx: AppContext) -> MemoryEmailSender:
    sender = ctx.email
    assert isinstance(sender, MemoryEmailSender)
    return sender


def test_magic_link_is_single_use_and_stored_only_as_a_hash(ctx: AppContext) -> None:
    client = new_client(ctx)
    assert (
        client.post("/auth/magic-link", json={"email": "Guest.One@Example.test"}).status_code == 202
    )
    token = email(ctx).token_for("guest.one@example.test")
    with ctx.sessions() as s:
        stored = s.scalars(select(MagicLink.token_hash)).all()
    assert hashlib.sha256(token.encode()).digest() in stored
    assert all(token.encode() not in h for h in stored)
    first = client.post("/auth/magic-link/verify", json={"token": token})
    assert first.status_code == 200
    again = new_client(ctx).post("/auth/magic-link/verify", json={"token": token})
    assert again.status_code == 400 and again.json()["detail"] == "link_invalid_or_expired"


def test_magic_link_expires(ctx: AppContext) -> None:
    client = new_client(ctx)
    client.post("/auth/magic-link", json={"email": "late@example.test"})
    token = email(ctx).token_for("late@example.test")
    with ctx.sessions() as s, s.begin():
        s.execute(
            update(MagicLink)
            .where(MagicLink.token_hash == hashlib.sha256(token.encode()).digest())
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert client.post("/auth/magic-link/verify", json={"token": token}).status_code == 400


def test_magic_link_requests_do_not_reveal_accounts_and_are_capped(ctx: AppContext) -> None:
    client = new_client(ctx)
    a = client.post("/auth/magic-link", json={"email": "nobody@example.test"})
    b = client.post("/auth/magic-link", json={"email": "nobody@example.test"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    for _ in range(10):
        client.post("/auth/magic-link", json={"email": "spam@example.test"})
    sent = [to for to, _ in email(ctx).sent if to == "spam@example.test"]
    assert len(sent) == ctx.settings.auth_magic_links_per_hour
    assert client.post("/auth/magic-link", json={"email": "not-an-email"}).status_code == 422


def test_session_cookie_flags_and_hash_only_storage(ctx: AppContext) -> None:
    client = new_client(ctx)
    client.post("/auth/magic-link", json={"email": "cookie@example.test"})
    response = client.post(
        "/auth/magic-link/verify", json={"token": email(ctx).token_for("cookie@example.test")}
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
    client = new_client(ctx)
    magic_sign_in(client, ctx, "rotate@example.test")
    old_token = client.cookies.get(SESSION_COOKIE)
    magic_sign_in(client, ctx, "rotate@example.test")
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
    phone, laptop = new_client(ctx), new_client(ctx)
    magic_sign_in(phone, ctx, "everywhere@example.test")
    magic_sign_in(laptop, ctx, "everywhere@example.test")
    assert laptop.get("/auth/me").status_code == 200
    assert phone.post("/auth/logout-everywhere").json()["sessions_ended"] == 2
    assert laptop.get("/auth/me").status_code == 401
    assert phone.get("/auth/me").status_code == 401


def test_passkey_registration_and_sign_in(ctx: AppContext) -> None:
    client = new_client(ctx)
    magic_sign_in(client, ctx, "passkey@example.test")
    assert client.get("/auth/me").json()["auth_method"] == "magic_link"
    authenticator = register_passkey(client)
    me = client.get("/auth/me").json()
    assert me["auth_method"] == "passkey" and me["has_passkey"] is True
    other_device = new_client(ctx)
    passkey_sign_in(other_device, authenticator)
    assert other_device.get("/auth/me").json()["user_id"] == me["user_id"]


def test_passkey_rejects_wrong_origin_replay_and_unknown_keys(ctx: AppContext) -> None:
    client = new_client(ctx)
    magic_sign_in(client, ctx, "strict@example.test")
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
    response = client.post(
        "/auth/magic-link",
        json={"email": "csrf@example.test"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    same = client.post(
        "/auth/magic-link",
        json={"email": "csrf@example.test"},
        headers={"Origin": ctx.settings.auth_rp_origin},
    )
    assert same.status_code == 202


def test_only_allow_listed_organisers_create_events(ctx: AppContext) -> None:
    someone = staff_client(ctx, "not-an-organiser@example.test")
    assert someone.post("/events", json={"name": "Mine", "country": "IN"}).status_code == 403
    assert someone.get("/auth/me").json()["can_create_events"] is False
