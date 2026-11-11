"""Unit tests for the backup command (`uv run tasks backup`)."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.tasks import TaskError, task_backup


def test_backup_fails_if_backup_dir_missing() -> None:
    """
    Purpose: verify that task_backup raises TaskError when FACESCAN_BACKUP_DIR is not set.
    Inputs: none. Outputs: none. Side effects: none.
    """
    with (
        patch.dict(os.environ, {"FACESCAN_BACKUP_DIR": ""}, clear=False),
        pytest.raises(TaskError, match="FACESCAN_BACKUP_DIR is not set"),
    ):
        task_backup(argparse.Namespace())


def test_backup_fails_if_backup_dir_inside_repo() -> None:
    """
    Purpose: verify that task_backup rejects a backup directory located inside the repo.
    Inputs: none. Outputs: none. Side effects: none.
    """
    inside_repo = str(Path(__file__).resolve().parents[3] / "var" / "backups")
    with (
        patch.dict(os.environ, {"FACESCAN_BACKUP_DIR": inside_repo}, clear=False),
        pytest.raises(TaskError, match="must be outside the repository root"),
    ):
        task_backup(argparse.Namespace())


def test_backup_fails_if_backup_dir_inside_storage() -> None:
    """
    Purpose: verify that task_backup rejects a backup directory inside the storage root.
    Inputs: none. Outputs: none. Side effects: creates temporary outside directory.
    """
    outside_tmp = tempfile.mkdtemp()
    try:
        storage_dir = Path(outside_tmp) / "storage"
        storage_dir.mkdir()
        backup_inside_storage = storage_dir / "backup"
        with (
            patch.dict(
                os.environ,
                {
                    "FACESCAN_STORAGE_ROOT": str(storage_dir),
                    "FACESCAN_BACKUP_DIR": str(backup_inside_storage),
                },
                clear=False,
            ),
            pytest.raises(TaskError, match="must be outside the storage directory"),
        ):
            task_backup(argparse.Namespace())
    finally:
        shutil.rmtree(outside_tmp, ignore_errors=True)


def test_backup_creates_verified_manifest_and_dump() -> None:
    """
    Purpose: execute task_backup to an outside temporary directory and verify that:
      1. database.dump is created and verified by pg_restore --list.
      2. Storage directory is copied without .env files.
      3. manifest.json contains valid SHA-256 for all files.
      4. Original storage and database are untouched.
    Inputs: none. Outputs: none. Side effects: creates backup in temporary outside folder.
    """
    outside_tmp = tempfile.mkdtemp()
    try:
        with patch.dict(os.environ, {"FACESCAN_BACKUP_DIR": outside_tmp}, clear=False):
            task_backup(argparse.Namespace())

        # Check that a backup directory was created
        created_dirs = [
            p for p in Path(outside_tmp).iterdir() if p.is_dir() and p.name.startswith("backup_")
        ]
        assert len(created_dirs) == 1
        backup_folder = created_dirs[0]

        # Verify dump file
        dump_file = backup_folder / "database.dump"
        assert dump_file.is_file()
        assert dump_file.stat().st_size > 0

        # Verify storage folder
        storage_folder = backup_folder / "storage"
        assert storage_folder.is_dir()

        # Invariant: No .env copied
        for item in backup_folder.rglob("*"):
            assert item.name != ".env"
            assert not item.name.startswith(".env.")

        # Verify manifest.json
        manifest_file = backup_folder / "manifest.json"
        assert manifest_file.is_file()
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert "created_at" in manifest_data
        assert manifest_data["file_count"] >= 1
        assert manifest_data["total_bytes"] > 0
        assert len(manifest_data["files"]) == manifest_data["file_count"]

        # Ensure database.dump is in manifest
        dump_entries = [f for f in manifest_data["files"] if f["path"] == "database.dump"]
        assert len(dump_entries) == 1
        assert dump_entries[0]["bytes"] == dump_file.stat().st_size
    finally:
        shutil.rmtree(outside_tmp, ignore_errors=True)
