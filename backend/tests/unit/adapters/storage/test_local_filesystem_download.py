from pathlib import Path
from uuid import uuid4

import pytest
from app.adapters.storage.local_filesystem_download import build_local_storage
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr

SIGNING_KEY = "s" * 48


@pytest.fixture
def storage_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        storage_root=str(tmp_path),
        storage_signing_key=SecretStr(SIGNING_KEY),
        storage_public_base_url="http://testserver",
    )


def test_route_not_mounted_without_signing_key(client: TestClient) -> None:
    response = client.get(f"/storage/facescan-photos/{uuid4()}/{'a' * 43}.jpg")
    assert response.status_code == 404


def test_signed_download_serves_file(storage_settings: Settings) -> None:
    storage = build_local_storage(storage_settings)
    event_id = uuid4()
    key = storage.put_object("facescan-photos", event_id, b"\xff\xd8jpeg", "image/jpeg")
    url = storage.create_signed_download_url("facescan-photos", event_id, key).url
    client = TestClient(create_app(storage_settings))
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == b"\xff\xd8jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_bad_signature_is_403(storage_settings: Settings) -> None:
    storage = build_local_storage(storage_settings)
    event_id = uuid4()
    key = storage.put_object("facescan-photos", event_id, b"x", "image/png")
    url = storage.create_signed_download_url("facescan-photos", event_id, key).url
    base, query = url.split("?")
    tampered = base + "?" + query.rsplit("signature=", 1)[0] + "signature=" + "B" * 43
    response = TestClient(create_app(storage_settings)).get(tampered)
    assert response.status_code == 403
    assert "Traceback" not in response.text


def test_missing_object_is_404(storage_settings: Settings) -> None:
    storage = build_local_storage(storage_settings)
    event_id = uuid4()
    key = storage.put_object("facescan-photos", event_id, b"x", "image/png")
    url = storage.create_signed_download_url("facescan-photos", event_id, key).url
    storage.delete_object("facescan-photos", event_id, key)
    assert TestClient(create_app(storage_settings)).get(url).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/storage/facescan-photos/not-a-uuid/" + "a" * 43 + ".jpg?expires=1&signature=" + "a" * 43,
        f"/storage/facescan-photos/{uuid4()}/bad.jpg?expires=1&signature=" + "a" * 43,
        f"/storage/facescan-photos/{uuid4()}/" + "a" * 43 + ".jpg?expires=1&signature=short",
        f"/storage/facescan-photos/{uuid4()}/" + "a" * 43 + ".jpg",
    ],
)
def test_invalid_input_is_422(storage_settings: Settings, path: str) -> None:
    assert TestClient(create_app(storage_settings)).get(path).status_code == 422


def test_short_signing_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        Settings(env="test", storage_signing_key=SecretStr("too-short"))
