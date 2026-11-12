"""Google ID token verification (pure function; no network)."""

import base64
import json

import pytest
from app.connectors.identity.google_identity_provider import (
    GoogleIdentityProvider,
    verify_id_token,
)
from app.interfaces.identity_provider import IdTokenRejectedError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

CLIENT_ID = "test-client.apps.googleusercontent.com"
NOW = 1_800_000_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token(key: RSAPrivateKey, kid: str = "k1", **overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "guest@example.test",
        "email_verified": True,
        "iat": NOW - 10,
        "exp": NOW + 3600,
    }
    claims.update(overrides)
    header = _b64(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(signature)}"


@pytest.fixture(scope="module")
def key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_valid_token_is_accepted(key: RSAPrivateKey) -> None:
    identity = verify_id_token(_token(key), {"k1": key.public_key()}, CLIENT_ID, NOW)
    assert identity.email == "guest@example.test"
    assert identity.subject == "1234567890"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-app"},
        {"iss": "https://evil.example"},
        {"exp": NOW - 3600},
        {"iat": NOW + 3600},
        {"email_verified": False},
        {"email": ""},
    ],
)
def test_wrong_claims_are_rejected(key: RSAPrivateKey, overrides: dict[str, object]) -> None:
    with pytest.raises(IdTokenRejectedError):
        verify_id_token(_token(key, **overrides), {"k1": key.public_key()}, CLIENT_ID, NOW)


def test_other_key_signature_is_rejected(key: RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(IdTokenRejectedError):
        verify_id_token(_token(other), {"k1": key.public_key()}, CLIENT_ID, NOW)


def test_unknown_kid_and_garbage_are_rejected(key: RSAPrivateKey) -> None:
    with pytest.raises(IdTokenRejectedError):
        verify_id_token(_token(key, kid="nope"), {"k1": key.public_key()}, CLIENT_ID, NOW)
    for garbage in ("", "a.b", "a.b.c", "x" * 5000):
        with pytest.raises(IdTokenRejectedError):
            verify_id_token(garbage, {"k1": key.public_key()}, CLIENT_ID, NOW)


def test_disabled_provider_rejects_everything(key: RSAPrivateKey) -> None:
    provider = GoogleIdentityProvider("")
    assert not provider.enabled
    with pytest.raises(IdTokenRejectedError):
        provider.verify(_token(key))
