"""EmailSender adapter for Gmail SMTP using Python's built-in smtplib.

Sends sign-in links securely via TLS (smtp.gmail.com:587).
Invariants:
- Never logs or prints the magic link or token.
- Never logs the full recipient email address or credentials.
- Logs only that an email was dispatched.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import Settings
from app.interfaces.capabilities import Capabilities

logger = logging.getLogger("facescan.email.smtp_gmail")


class SmtpGmailEmailSender:
    """Delivers sign-in magic links via SMTP over TLS."""

    def __init__(self, settings: Settings) -> None:
        """
        Purpose: initialize SMTP sender with credentials from Settings.
        Inputs: settings. Outputs: none. Side effects: none.
        """
        self._user = settings.email_smtp_user or ""
        self._password = (
            settings.email_smtp_password.get_secret_value()
            if settings.email_smtp_password is not None
            else ""
        )
        self._host = settings.email_smtp_host
        self._port = settings.email_smtp_port
        self._use_tls = settings.email_smtp_use_tls

    @property
    def capabilities(self) -> Capabilities:
        """Static description: external SMTP adapter."""
        return Capabilities(adapter_name="smtp_gmail")

    def send_sign_in_link(self, to_address: str, link: str, expires_minutes: int) -> None:
        """
        Purpose: send single-use sign-in link to guest via SMTP.
        Inputs: to_address, link, expires_minutes.
        Outputs: none.
        Side effects: sends email via SMTP socket, writes generic audit log entry.
        """
        msg = EmailMessage()
        msg["Subject"] = "Your FaceScan sign-in link"
        msg["From"] = self._user or "no-reply@facescan.app"
        msg["To"] = to_address
        msg.set_content(
            "Hello,\n\n"
            f"Use the link below to sign in to FaceScan (valid for {expires_minutes} minutes, "
            f"single use):\n\n"
            f"{link}\n\n"
            "If you did not request this link, you can safely ignore this email.\n"
        )

        try:
            with smtplib.SMTP(self._host, self._port, timeout=10.0) as server:
                if self._use_tls:
                    server.starttls()
                if self._user and self._password:
                    server.login(self._user, self._password)
                server.send_message(msg)
            logger.info("sign-in email sent successfully via SMTP")
        except Exception as exc:
            # Privacy invariant: never log recipient or link on failure
            logger.error("failed to send sign-in email via SMTP: %s", type(exc).__name__)
            raise
