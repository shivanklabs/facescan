"""Unit tests for phone test mode (public), CF-Connecting-IP resolution,
and Gmail SMTP email adapter.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.adapters.email.smtp_gmail_email import SmtpGmailEmailSender
from app.core.config import Settings
from app.core.rate_limit import extract_client_ip
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from scripts.tasks import TaskError, check_required_env_vars, find_cloudflared


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


def test_public_mode_serves_api_and_spa() -> None:
    """
    Purpose: verify that API endpoints are accessible both directly and under /api prefix,
        ensuring relative frontend calls succeed in public mode.
    Inputs: none. Outputs: none. Side effects: creates test app and client.
    """
    settings = Settings(env="public")
    mock_ctx = MagicMock()
    app = create_app(settings=settings, ctx=mock_ctx)
    client = TestClient(app)

    # Health probe responds on both paths
    assert client.get("/health").status_code == 200
    assert client.get("/api/health").status_code == 200

    # Auth endpoint responds on both paths (401 unauthorized, not 404)
    assert client.get("/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 401

    # Unmapped /api route returns 404 JSON, not HTML fallback
    res = client.get("/api/nonexistent_route")
    assert res.status_code == 404
    assert res.headers.get("content-type", "").startswith("application/json")


def test_find_cloudflared_lookup() -> None:
    """
    Purpose: verify find_cloudflared discovers binary from PATH or winget install folders.
    Inputs: none. Outputs: none. Side effects: none (mocked).
    """
    # 1. When shutil.which finds it
    with patch("shutil.which", return_value="/usr/local/bin/cloudflared"):
        assert find_cloudflared() == "/usr/local/bin/cloudflared"

    # 2. When not on PATH, checks Windows winget candidates
    with (
        patch("shutil.which", return_value=None),
        patch("sys.platform", "win32"),
        patch("pathlib.Path.is_file", return_value=True),
    ):
        found = find_cloudflared()
        assert found is not None
        assert "cloudflared.exe" in found


def test_check_required_env_vars_preflight() -> None:
    """
    Purpose: verify preflight checks that required env vars exist and are non-empty,
        raising TaskError without printing values.
    Inputs: none. Outputs: none. Side effects: none.
    """
    # All present
    env_valid = {"FOO": "value1", "BAR": "value2"}
    check_required_env_vars(env_valid, ["FOO", "BAR"], "test")

    # Missing and empty
    env_invalid = {"FOO": "value1", "BAR": "", "SECRET_KEY": "supersecret"}
    with pytest.raises(TaskError) as exc_info:
        check_required_env_vars(env_invalid, ["FOO", "BAR", "BAZ"], "test")

    msg = str(exc_info.value)
    assert "BAR" in msg
    assert "BAZ" in msg
    assert "FOO" not in msg
    assert "supersecret" not in msg  # Never prints values!


def test_spa_serve_path_traversal_prevention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: verify serve_spa prevents path traversal in public mode
    and never serves files outside dist.
    Inputs: tmp_path, monkeypatch. Outputs: None. Side effects: creates temporary files.
    """
    secret_file = tmp_path / ".env"
    secret_file.write_text("FACESCAN_DB_PASSWORD=secret_password_123\n", encoding="utf-8")

    fake_dist = tmp_path / "frontend" / "dist"
    fake_dist.mkdir(parents=True)
    index_file = fake_dist / "index.html"
    index_file.write_text("<html>SPA Index</html>", encoding="utf-8")
    asset_file = fake_dist / "app.js"
    asset_file.write_text("console.log('facescan');", encoding="utf-8")

    monkeypatch.setattr("app.main._get_dist_dir", lambda: fake_dist)

    settings = Settings(env="public")
    app = create_app(settings)
    client = TestClient(app)

    # 1. Normal asset inside dist is served
    resp = client.get("/app.js")
    assert resp.status_code == 200
    assert "console.log('facescan');" in resp.text

    # 2. Path traversal attempts must never return .env content; they fall through to index.html
    traversal_paths = [
        "/..%2f..%2f.env",
        "/../../.env",
        "/%2e%2e/%2e%2e/.env",
    ]
    for path in traversal_paths:
        res = client.get(path)
        assert "secret_password_123" not in res.text
        assert "<html>SPA Index</html>" in res.text
