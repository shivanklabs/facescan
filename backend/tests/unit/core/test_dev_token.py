"""TEMPORARY dev-only admin token: accepted only in dev; the API refuses to start elsewhere."""

import pytest
from app.core.config import Settings
from app.core.security import DevTokenNotAllowedError, check_dev_token_allowed
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr

TOKEN = SecretStr("d" * 40)


@pytest.mark.parametrize("env", ["test", "prod"])
def test_api_refuses_to_start_with_dev_token_outside_dev(env: str) -> None:
    settings = Settings(_env_file=None, env=env, dev_admin_token=TOKEN)
    with pytest.raises(DevTokenNotAllowedError):
        check_dev_token_allowed(settings)
    with pytest.raises(DevTokenNotAllowedError):
        create_app(settings)


def test_without_token_the_dev_routes_do_not_exist(client: TestClient) -> None:
    assert client.get("/events").status_code == 404
    assert client.post("/events", json={"name": "x", "country": "IN"}).status_code == 404


def test_short_tokens_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, env="dev", dev_admin_token=SecretStr("short"))
