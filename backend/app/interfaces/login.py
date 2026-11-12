"""Port: A passwordless login method (passkey, email link; SMS OTP later)."""

from typing import Protocol

from app.interfaces.capabilities import Capabilities


class Login(Protocol):
    """TODO: define the methods of this port."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...
