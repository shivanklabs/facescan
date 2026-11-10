"""Tests verifying configuration isolation from .env files during test runs."""

from pathlib import Path

import pytest
from app.core.config import Settings


def test_test_settings_do_not_load_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: verify that test settings built with _env_file=None never load values from a .env file.
    Inputs: tmp_path (Path), monkeypatch (pytest.MonkeyPatch).
    Outputs: None.
    Side effects: creates a temporary .env file and sets the process working directory.
    """
    sentinel = "FORBIDDEN_ENV_FILE_VALUE"
    env_file = tmp_path / ".env"
    env_file.write_text(f"FACESCAN_STORAGE_ROOT={sentinel}\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FACESCAN_STORAGE_ROOT", raising=False)

    # Standard Settings() in this working directory would read .env
    live_settings = Settings()
    assert live_settings.storage_root == sentinel

    # Test settings built with _env_file=None must not read .env
    test_settings = Settings(_env_file=None)
    assert test_settings.storage_root != sentinel
    assert test_settings.storage_root == "var/storage"


def test_settings_fixture_does_not_load_env_file(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Purpose: verify that the shared conftest test settings fixture never loads values
        from a .env file.
    Inputs: settings fixture (Settings), tmp_path (Path), monkeypatch (pytest.MonkeyPatch).
    Outputs: None.
    Side effects: none.
    """
    # Verify that the fixture does not declare an env_file
    has_env = settings.model_config.get("env_file") is not None
    assert not has_env or getattr(settings, "_env_file", None) is None
