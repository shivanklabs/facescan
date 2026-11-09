"""Port: sends sign-in emails (magic links). No email service is configured yet."""

from typing import Protocol

from app.ports.capabilities import Capabilities


class EmailSender(Protocol):
    """Delivers a sign-in link to one address."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description of the adapter implementing this port."""
        ...

    def send_sign_in_link(self, to_address: str, link: str, expires_minutes: int) -> None:
        """
        Purpose: deliver a single-use sign-in link.
        Side effects: sends (or, for the console adapter, prints) one message.
        """
        ...
