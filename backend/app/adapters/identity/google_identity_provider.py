"""Google sign-in: verify a Google ID token (RS256 JWT) with Google's published keys.

No Google SDK: the signature is checked with `cryptography` (RSA PKCS#1 v1.5, SHA-256) and the
keys are fetched with httpx2 from Google's JWKS endpoint, cached for an hour.

Invariants:
- Only tokens whose `aud` is FACESCAN_GOOGLE_CLIENT_ID, whose `iss` is Google and whose email
  is verified are accepted; expiry is checked with a small clock-skew allowance.
- The token, the email and the keys are never logged.
- The ID token is checked locally; the only request to Google fetches its public keys and
  carries no user data.
"""

import base64
import json
import threading
import time
from collections.abc import Mapping

import httpx2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers

from app.ports.capabilities import Capabilities
from app.ports.identity_provider import IdentityProvider, IdTokenRejectedError, VerifiedIdentity

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
KEYS_TTL_SECONDS = 3600
CLOCK_SKEW_SECONDS = 60
MAX_TOKEN_CHARS = 4096


def _b64url_decode(part: str) -> bytes:
    """Purpose: decode one base64url JWT segment (padding restored). Raises IdTokenRejectedError."""
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except ValueError as exc:
        raise IdTokenRejectedError("malformed token") from exc


def _json_object(raw: bytes) -> dict[str, object]:
    """Purpose: parse a JSON object. Raises IdTokenRejectedError for anything else."""
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IdTokenRejectedError("malformed token") from exc
    if not isinstance(value, dict):
        raise IdTokenRejectedError("malformed token")
    return {str(k): v for k, v in value.items()}


def rsa_key_from_jwk(jwk: Mapping[str, object]) -> RSAPublicKey:
    """
    Purpose: build an RSA public key from a JWK (kty RSA, n, e).
    Inputs: one JWK mapping. Outputs: RSAPublicKey. Raises IdTokenRejectedError.
    """
    n, e = jwk.get("n"), jwk.get("e")
    if jwk.get("kty") != "RSA" or not isinstance(n, str) or not isinstance(e, str):
        raise IdTokenRejectedError("unusable signing key")
    numbers = RSAPublicNumbers(
        int.from_bytes(_b64url_decode(e), "big"), int.from_bytes(_b64url_decode(n), "big")
    )
    return numbers.public_key()


def verify_id_token(
    token: str, keys: Mapping[str, RSAPublicKey], client_id: str, now: float
) -> VerifiedIdentity:
    """
    Purpose: verify a Google ID token against already-fetched keys (pure; easy to test).
    Inputs: compact JWT, keys by `kid`, expected audience, current unix time.
    Outputs: VerifiedIdentity. Raises IdTokenRejectedError with a generic reason.
    Side effects: none.
    """
    if not token or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise IdTokenRejectedError("malformed token")
    header_b64, payload_b64, signature_b64 = token.split(".")
    header = _json_object(_b64url_decode(header_b64))
    if header.get("alg") != "RS256":
        raise IdTokenRejectedError("unsupported algorithm")
    kid = header.get("kid")
    key = keys.get(kid) if isinstance(kid, str) else None
    if key is None:
        raise IdTokenRejectedError("unknown signing key")
    try:
        key.verify(
            _b64url_decode(signature_b64),
            f"{header_b64}.{payload_b64}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, UnicodeEncodeError) as exc:
        raise IdTokenRejectedError("bad signature") from exc

    claims = _json_object(_b64url_decode(payload_b64))
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise IdTokenRejectedError("wrong issuer")
    if claims.get("aud") != client_id:
        raise IdTokenRejectedError("wrong audience")
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int):
        raise IdTokenRejectedError("missing time claims")
    if exp + CLOCK_SKEW_SECONDS < now or iat - CLOCK_SKEW_SECONDS > now:
        raise IdTokenRejectedError("expired token")
    subject, email = claims.get("sub"), claims.get("email")
    if not isinstance(subject, str) or not subject or not isinstance(email, str) or not email:
        raise IdTokenRejectedError("missing identity claims")
    if claims.get("email_verified") is not True:
        raise IdTokenRejectedError("email not verified")
    return VerifiedIdentity(subject=subject, email=email)


class GoogleIdentityProvider(IdentityProvider):
    """Google ID token verifier with an in-process JWKS cache."""

    def __init__(self, client_id: str, timeout_seconds: float = 5.0) -> None:
        """
        Purpose: configure the verifier. Inputs: OAuth client id ("" disables), HTTP timeout.
        Outputs: none. Side effects: none.
        """
        self._client_id = client_id.strip()
        self._timeout = timeout_seconds
        self._keys: dict[str, RSAPublicKey] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> Capabilities:
        """Static description: fetches Google's public keys (no user data sent)."""
        return Capabilities(adapter_name="google", sends_data_off_host=False)

    @property
    def enabled(self) -> bool:
        """True when FACESCAN_GOOGLE_CLIENT_ID is set."""
        return bool(self._client_id)

    def _fetch_keys(self) -> dict[str, RSAPublicKey]:
        """
        Purpose: download Google's JWKS (two attempts). Outputs: keys by kid.
        Raises IdTokenRejectedError when the keys cannot be fetched or parsed.
        Side effects: one or two HTTPS GET requests.
        """
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                response = httpx2.get(GOOGLE_JWKS_URL, timeout=self._timeout)
                response.raise_for_status()
                body = _json_object(response.content)
                raw_keys = body.get("keys")
                if not isinstance(raw_keys, list):
                    raise IdTokenRejectedError("bad key set")
                keys: dict[str, RSAPublicKey] = {}
                for item in raw_keys:
                    if isinstance(item, dict) and isinstance(item.get("kid"), str):
                        keys[str(item["kid"])] = rsa_key_from_jwk(item)
                if keys:
                    return keys
                raise IdTokenRejectedError("empty key set")
            except (httpx2.HTTPError, IdTokenRejectedError) as exc:
                last_error = exc
        raise IdTokenRejectedError("could not fetch Google keys") from last_error

    def _current_keys(self, kid: str | None) -> dict[str, RSAPublicKey]:
        """
        Purpose: cached keys; refetched after the TTL or when the token's kid is unknown
            (Google rotates keys). Side effects: may fetch keys.
        """
        with self._lock:
            stale = time.monotonic() - self._fetched_at > KEYS_TTL_SECONDS
            if not self._keys or stale or (kid is not None and kid not in self._keys):
                self._keys = self._fetch_keys()
                self._fetched_at = time.monotonic()
            return dict(self._keys)

    def verify(self, id_token: str) -> VerifiedIdentity:
        """
        Purpose: verify a Google ID token for this app.
        Outputs: VerifiedIdentity. Raises IdTokenRejectedError (also when not configured).
        Side effects: may fetch Google's public keys.
        """
        if not self.enabled:
            raise IdTokenRejectedError("Google sign-in is not configured")
        if not id_token or len(id_token) > MAX_TOKEN_CHARS or id_token.count(".") != 2:
            raise IdTokenRejectedError("malformed token")
        header = _json_object(_b64url_decode(id_token.split(".", 1)[0]))
        kid = header.get("kid")
        keys = self._current_keys(kid if isinstance(kid, str) else None)
        return verify_id_token(id_token, keys, self._client_id, time.time())
