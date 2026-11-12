"""Tests for startup cleanup of orphaned upload spool files."""

import base64
import os
import secrets
import time
from pathlib import Path

from app.core.settings import Settings
from app.core.upload_parsing import clean_orphaned_spool_files
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr


def test_clean_orphaned_spool_files(tmp_path: Path) -> None:
    """
    Purpose: verify that old files are removed while recent files, subdirectories,
        symlinks, and files outside the spool folder are untouched.
    Inputs: tmp_path fixture.
    Outputs: None.
    Side effects: creates files and runs cleanup.
    """
    spool_dir = tmp_path / "spool"
    spool_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    now = time.time()
    one_hour = 3600

    # 1. Old spool file (> 1 hour old)
    old_file = spool_dir / "old_upload.tmp"
    old_file.write_bytes(b"old data")
    os.utime(old_file, (now - one_hour - 120, now - one_hour - 120))

    # 2. Recent spool file (< 1 hour old)
    recent_file = spool_dir / "recent_upload.tmp"
    recent_file.write_bytes(b"recent data")
    os.utime(recent_file, (now - 60, now - 60))

    # 3. Subfolder with a file inside it
    subfolder = spool_dir / "subfolder"
    subfolder.mkdir()
    file_in_sub = subfolder / "nested.tmp"
    file_in_sub.write_bytes(b"nested")
    os.utime(file_in_sub, (now - one_hour - 120, now - one_hour - 120))

    # 4. File outside the spool folder
    outside_file = outside_dir / "preserve.txt"
    outside_file.write_bytes(b"outside")
    os.utime(outside_file, (now - one_hour - 120, now - one_hour - 120))

    # 5. Symlink to outside file (if OS allows)
    symlink_file = spool_dir / "symlink.tmp"
    try:
        symlink_file.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        symlink_file = None

    # Run cleanup
    removed = clean_orphaned_spool_files(spool_dir, max_age_seconds=one_hour, now=now)

    assert removed == 1
    assert not old_file.exists(), "old spool file must be deleted"
    assert recent_file.exists(), "recent spool file must be preserved"
    assert subfolder.exists(), "subfolder must not be deleted"
    assert file_in_sub.exists(), "files inside subfolders must not be touched"
    assert outside_file.exists(), "files outside spool directory must not be touched"
    if symlink_file is not None:
        assert symlink_file.is_symlink(), "symlinks must not be followed or deleted"
        assert outside_file.exists(), "target of symlink must not be deleted"


def test_api_startup_cleans_spool_files(tmp_path: Path) -> None:
    """
    Purpose: verify that API startup lifespan cleans orphaned spool files and logs count only.
    Inputs: tmp_path fixture.
    Outputs: None.
    Side effects: creates files and starts test client.
    """
    spool_dir = tmp_path / "var_tmp_uploads"
    spool_dir.mkdir(parents=True)

    now = time.time()
    old_file = spool_dir / "orphaned_crash.tmp"
    old_file.write_bytes(b"crash-data")
    os.utime(old_file, (now - 7200, now - 7200))

    recent_file = spool_dir / "active.tmp"
    recent_file.write_bytes(b"active-data")
    os.utime(recent_file, (now - 60, now - 60))

    master_key = base64.b64encode(secrets.token_bytes(32)).decode()
    signing_key = base64.b64encode(secrets.token_bytes(32)).decode()
    settings = Settings(
        _env_file=None,
        upload_spool_dir=str(spool_dir),
        upload_spool_max_age_seconds=3600,
        env="dev",
        auth_rp_origin="http://localhost:5173",
        keys_master_key=SecretStr(master_key),
        storage_signing_key=SecretStr(signing_key),
    )

    app = create_app(settings)
    with TestClient(app):
        # Lifespan startup ran
        assert not old_file.exists(), "old file must be deleted at API startup"
        assert recent_file.exists(), "recent file must remain at API startup"
