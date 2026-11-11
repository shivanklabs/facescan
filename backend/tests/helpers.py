"""Test helpers: captured email, a software passkey authenticator, sign-in shortcuts.

The SoftAuthenticator produces real WebAuthn responses (P-256 keys, "none" attestation,
signed assertions) so the server-side passkey code is exercised end to end by py_webauthn.
Uses webauthn.helpers for CBOR encoding and base64url conversions.
"""

import hashlib
import json
import re
import secrets
import struct
import uuid
from dataclasses import dataclass, field

from app.core.context import AppContext
from app.core.crypto import new_data_key
from app.db.event_scope import event_transaction
from app.modules.events.models import Event, EventKey
from app.ports.capabilities import Capabilities
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, encode_cbor

ORIGIN = "http://localhost:5173"
RP_ID = "localhost"
ORGANISER_EMAIL = "organiser@example.test"


class MemoryEmailSender:
    """EmailSender that keeps links in memory (tests read them back)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(adapter_name="memory")

    def send_sign_in_link(self, to_address: str, link: str, expires_minutes: int) -> None:
        self.sent.append((to_address, link))

    def token_for(self, address: str) -> str:
        for to, link in reversed(self.sent):
            if to == address:
                match = re.search(r"#token=([A-Za-z0-9_\-]+)$", link)
                assert match, "no token in link"
                return match.group(1)
        raise AssertionError(f"no link sent to {address}")


# ---------------------------------------------------------------- WebAuthn Authenticator


@dataclass
class SoftAuthenticator:
    """One discoverable passkey on a software authenticator."""

    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    sign_count: int = 0
    user_handle: bytes = b""

    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}
        ).encode()

    def create(self, options: dict[str, object], origin: str = ORIGIN) -> dict[str, object]:
        user = options["user"]
        assert isinstance(user, dict)
        self.user_handle = base64url_to_bytes(str(user["id"]))
        numbers = self.key.public_key().public_numbers()
        cose = {
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([0x45])  # UP | UV | AT
            + struct.pack(">I", self.sign_count)
            + bytes(16)  # AAGUID
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + encode_cbor(cose)
        )
        client_data = self._client_data("webauthn.create", str(options["challenge"]), origin)
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(
                    encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
                ),
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict[str, object], origin: str = ORIGIN) -> dict[str, object]:
        self.sign_count += 1
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([0x05])
            + struct.pack(">I", self.sign_count)
        )
        client_data = self._client_data("webauthn.get", str(options["challenge"]), origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(self.user_handle),
            },
            "clientExtensionResults": {},
        }


# ---------------------------------------------------------------- sign-in shortcuts


def new_client(ctx: AppContext) -> TestClient:
    """A browser-like client (https so Secure cookies are sent)."""
    from app.main import create_app

    return TestClient(create_app(ctx.settings, ctx=ctx), base_url="https://testserver")


def magic_sign_in(
    client: TestClient, ctx: AppContext, address: str, next_path: str | None = None
) -> dict[str, object]:
    assert (
        client.post("/auth/magic-link", json={"email": address, "next": next_path}).status_code
        == 202
    )
    email = ctx.email
    assert isinstance(email, MemoryEmailSender)
    response = client.post("/auth/magic-link/verify", json={"token": email.token_for(address)})
    assert response.status_code == 200, response.text
    return dict(response.json())


def register_passkey(client: TestClient) -> SoftAuthenticator:
    authenticator = SoftAuthenticator()
    options = client.post("/auth/passkey/register/options").json()
    response = client.post(
        "/auth/passkey/register/verify", json={"credential": authenticator.create(options)}
    )
    assert response.status_code == 200, response.text
    return authenticator


def passkey_sign_in(client: TestClient, authenticator: SoftAuthenticator) -> None:
    options = client.post("/auth/passkey/login/options").json()
    response = client.post(
        "/auth/passkey/login/verify", json={"credential": authenticator.get(options)}
    )
    assert response.status_code == 200, response.text


def staff_client(ctx: AppContext, address: str) -> TestClient:
    """A signed-in staff member with a passkey session."""
    client = new_client(ctx)
    magic_sign_in(client, ctx, address)
    register_passkey(client)
    return client


def guest_client(
    ctx: AppContext,
    address: str,
    event_id: str,
    invite_url: str,
    adult: bool = True,
    consent: bool = True,
) -> TestClient:
    """A guest who opened the invite without email, joined, and consented."""
    key = invite_url.split("?k=", 1)[1]
    client = new_client(ctx)
    join_resp = client.post(f"/invites/{event_id}/join", json={"key": key})
    assert join_resp.status_code == 200, join_resp.text
    if not adult:
        assert client.post("/auth/age", json={"is_adult": False}).status_code == 204
    if consent:
        notice = client.get("/consent/notice").json()
        response = client.post(
            f"/events/{event_id}/consent",
            json={"notice_version": notice["version"], "language": "en", "face_search": True},
        )
        assert response.status_code == 204, response.text
    return client


def frames_body(*frames: bytes) -> bytes:
    """Length-prefixed frames (application/x-facescan-frames)."""
    return b"".join(struct.pack(">I", len(f)) + f for f in frames)


FRAMES_HEADERS = {"Content-Type": "application/x-facescan-frames"}


def quick_event(ctx: AppContext, name: str = "Test event") -> uuid.UUID:
    """An event with a data key and no members (for database-level tests)."""
    event_id = uuid.uuid4()
    with event_transaction(ctx.sessions, event_id) as session:
        session.add(Event(id=event_id, event_id=event_id, name=name, country="IN"))
        session.flush()
        session.add(
            EventKey(
                event_id=event_id,
                key_version=1,
                wrapped_key=ctx.keys.wrap(event_id, new_data_key()),
                master_key_id=ctx.keys.master_key_id,
            )
        )
    return event_id


def searchable_event(
    ctx: AppContext, organiser: TestClient, photos: list[bytes], name: str = "Wedding"
) -> tuple[str, str, list[str]]:
    """Create an event, upload and approve photos, run the worker, create an invite.
    Outputs: (event_id, invite_url, photo_ids in upload order)."""
    from app.db.event_scope import DISPATCH_SCOPE, scoped_transaction
    from app.worker import run_one
    from sqlalchemy import text

    with scoped_transaction(ctx.sessions, DISPATCH_SCOPE) as s:  # leftovers of other tests
        s.execute(text("UPDATE jobs SET status = 'done' WHERE status IN ('queued', 'running')"))
    event_id = organiser.post("/events", json={"name": name, "country": "IN"}).json()["event_id"]
    files = [("files", (f"p{i}.png", data, "image/png")) for i, data in enumerate(photos)]
    body = organiser.post(f"/events/{event_id}/photos", files=files).json()
    approved = organiser.post(f"/events/{event_id}/collections/{body['collection_id']}/approve")
    assert approved.status_code == 200, approved.text
    while run_one(ctx, "test-worker"):
        pass
    invite = organiser.post(f"/events/{event_id}/invites", json={}).json()
    return event_id, str(invite["url"]), list(body["photo_ids"])
