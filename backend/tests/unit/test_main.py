from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_every_response_carries_security_headers_and_is_not_cached(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "camera=(self)" in response.headers["Permissions-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    # Errors too (the session check answers before any route code runs).
    assert client.get("/auth/me").headers["X-Frame-Options"] == "DENY"
