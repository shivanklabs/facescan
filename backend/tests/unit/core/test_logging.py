from app.core.logging import redact


def test_redacts_email_and_bearer() -> None:
    out = redact("user a.b@example.com sent Bearer abc.def")
    assert "example.com" not in out
    assert "abc.def" not in out
