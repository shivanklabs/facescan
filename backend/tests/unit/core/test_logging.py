import logging

from app.core.logging import ArgsRedactionFilter, redact


def test_redacts_email_and_bearer() -> None:
    out = redact("user a.b@example.com sent Bearer abc.def")
    assert "example.com" not in out
    assert "abc.def" not in out


def test_redacts_signed_url_signature() -> None:
    out = redact("GET /storage/b/e/k.jpg?expires=1&signature=abcDEF_-123 HTTP/1.1")
    assert "abcDEF_-123" not in out
    assert "expires=1&signature=[redacted]" in out


def test_access_log_args_are_redacted_and_kept_as_tuple() -> None:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s"', None, None
    )
    record.args = ("127.0.0.1", "GET", "/storage/x?expires=1&signature=secretsig")
    assert ArgsRedactionFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert "secretsig" not in record.getMessage()
