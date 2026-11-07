"""Shared fixtures."""

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", worker_poll_interval_seconds=0.01)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
