"""EmailSender adapter for development: prints the sign-in link to the API terminal.

DEV ONLY. It writes to stdout with print(), not to the logging system, so links never reach
log files or log shippers; build_context refuses this adapter outside FACESCAN_ENV=dev.
The address is masked in the printed line.
"""

import sys

from app.interfaces.capabilities import Capabilities


def mask_email(address: str) -> str:
    """Purpose: a@example.com -> a***@example.com (for the console line only)."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class ConsoleEmailSender:
    """Prints magic links to the terminal (development only)."""

    @property
    def capabilities(self) -> Capabilities:
        """Static description: free, local, dev only."""
        return Capabilities(adapter_name="console")

    def send_sign_in_link(self, to_address: str, link: str, expires_minutes: int) -> None:
        """Purpose: print the link. Side effects: writes one block to stdout."""
        print(
            "\n=== FaceScan DEV sign-in link (console email adapter) ===\n"
            f"to: {mask_email(to_address)}  (expires in {expires_minutes} minutes, single use)\n"
            f"{link}\n"
            "=========================================================\n",
            file=sys.stdout,
            flush=True,
        )
