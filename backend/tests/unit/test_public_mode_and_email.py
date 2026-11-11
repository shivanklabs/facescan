"""Unit tests for phone test mode (public), CF-Connecting-IP resolution,
and Gmail SMTP email adapter.
"""

from unittest.mock import MagicMock, patch

from app.adapters.email.smtp_gmail_email import SmtpGmailEmailSender
from app.core.config import Settings
from app.core.rate_limit import extract_client_ip
from app.main import create_app
from pydantic import SecretStr
from starlette.requests import Request


def _mock_request(client_host: str, headers: dict[str, str] | None = None) -> Request:
    """Purpose: build mock Request for IP resolution testing."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "client": (client_host, 12345),
        "headers": raw_headers,
    }
    return Request(scope)


def test_extract_client_ip_from_tunnel() -> None:
    """
    Purpose: verify that CF-Connecting-IP is used ONLY when request originates from local tunnel.
    """
    # 1. From local tunnel (127.0.0.1) with CF header -> returns CF IP
    req1 = _mock_request("127.0.0.1", {"CF-Connecting-IP": "203.0.113.42"})
    assert extract_client_ip(req1) == "203.0.113.42"

    # 2. From IPv6 local tunnel (::1) with CF header -> returns CF IP
    req2 = _mock_request("::1", {"CF-Connecting-IP": "198.51.100.7"})
    assert extract_client_ip(req2) == "198.51.100.7"

    # 3. Direct external client trying to spoof CF-Connecting-IP -> ignored, uses real client host
    req3 = _mock_request("192.168.1.100", {"CF-Connecting-IP": "1.2.3.4"})
    assert extract_client_ip(req3) == "192.168.1.100"

    # 4. Local request without CF header -> uses local IP
    req4 = _mock_request("127.0.0.1", {})
    assert extract_client_ip(req4) == "127.0.0.1"


def test_smtp_gmail_email_sender_flow() -> None:
    """
    Purpose: verify SmtpGmailEmailSender delivers message via TLS without leaking secrets.
    """
    settings = Settings(
        email_adapter="smtp_gmail",
        email_smtp_user="test_organiser@gmail.com",
        email_smtp_password=SecretStr("supersecretapppassword"),
        email_smtp_host="localhost",
        email_smtp_port=2525,
        email_smtp_use_tls=True,
    )
    sender = SmtpGmailEmailSender(settings)
    assert sender.capabilities.adapter_name == "smtp_gmail"

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_instance = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_instance

        sender.send_sign_in_link(
            to_address="guest@example.test",
            link="https://abc.trycloudflare.com/auth/callback?t=test_token_123",
            expires_minutes=15,
        )

        # Verified TLS and login called
        mock_instance.starttls.assert_called_once()
        mock_instance.login.assert_called_once_with(
            "test_organiser@gmail.com", "supersecretapppassword"
        )

        # Verified message sent with correct recipient and subject
        mock_instance.send_message.assert_called_once()
        sent_msg = mock_instance.send_message.call_args[0][0]
        assert sent_msg["To"] == "guest@example.test"
        assert sent_msg["Subject"] == "Your FaceScan sign-in link"
        assert "test_token_123" in sent_msg.get_content()


def test_public_mode_disables_docs_and_openapi() -> None:
    """
    Purpose: verify that API documentation and openapi.json are disabled in public mode.
    Inputs: none. Outputs: none. Side effects: instantiates FastAPI app with mock context.
    """
    settings = Settings(
        env="public",
        auth_cookie_secure=True,
    )
    mock_ctx = MagicMock()
    app = create_app(settings=settings, ctx=mock_ctx)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
