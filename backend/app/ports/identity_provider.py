"""Port: an outside identity provider that signs ID tokens (Google sign-in)."""

from dataclasses import dataclass
from typing import Protocol

from app.ports.capabilities import Capabilities


class IdTokenRejectedError(Exception):
    """The ID token is malformed, expired, not for this app, or its signature is wrong."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """The parts of a verified ID token FaceScan uses. The email is verified by the provider."""

    subject: str
    email: str


class IdentityProvider(Protocol):
    """Verifies an ID token issued to this app's client id."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter."""
        ...

    @property
    def enabled(self) -> bool:
        """True when a client id is configured."""
        ...

    def verify(self, id_token: str) -> VerifiedIdentity:
        """
        Purpose: check the token's signature, issuer, audience, expiry and verified email.
        Outputs: VerifiedIdentity. Raises IdTokenRejectedError.
        Side effects: may fetch the provider's public keys (cached); never logs the token.
        """
        ...
