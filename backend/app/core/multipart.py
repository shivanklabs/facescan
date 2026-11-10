"""Multipart parsing for event-photo uploads with a controlled spool folder.

Starlette keeps each file part in memory up to 1 MB and then rolls it over to a temporary
file in the system temp folder. For event photos we keep that behaviour but put the spool
files under FACESCAN_UPLOAD_SPOOL_DIR (default var/tmp/uploads, git-ignored); they are
deleted when the form is closed at the end of the request. Selfies never use multipart: they
are read as a raw body into memory (app/modules/matching/router.py).
"""

import logging
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast

from starlette.datastructures import FormData, Headers
from starlette.formparsers import MultiPartException, MultiPartParser

logger = logging.getLogger("facescan.uploads")
SPOOL_IN_MEMORY_BYTES = 1024 * 1024


class SpoolDirMultiPartParser(MultiPartParser):
    """Starlette's parser, with file parts spooled to a given folder instead of system temp."""

    def __init__(
        self,
        headers: Headers,
        stream: AsyncGenerator[bytes, None],
        spool_dir: Path,
        *,
        max_files: int,
        max_fields: int,
        max_part_size: int,
    ) -> None:
        """
        Purpose: configure limits and the spool folder.
        Side effects: creates the spool folder if needed.
        """
        super().__init__(
            headers,
            stream,
            max_files=max_files,
            max_fields=max_fields,
            max_part_size=max_part_size,
        )
        spool_dir.mkdir(parents=True, exist_ok=True)
        self.spool_dir = spool_dir

    def new_spool(self) -> SpooledTemporaryFile[bytes]:
        """Purpose: a spool file that rolls over into spool_dir (tests may wrap this)."""
        return SpooledTemporaryFile(
            max_size=SPOOL_IN_MEMORY_BYTES, mode="w+b", dir=str(self.spool_dir)
        )

    def on_headers_finished(self) -> None:
        """Purpose: as Starlette, then swap the new file part's spool for one in spool_dir."""
        super().on_headers_finished()
        upload = self._current_part.file
        if upload is None:
            return
        # Starlette just appended this part's default spool; it is still empty and in memory.
        self._files_to_close_on_error.pop().close()
        spool = self.new_spool()
        self._files_to_close_on_error.append(spool)
        upload.file = cast(BinaryIO, spool)  # SpooledTemporaryFile is binary file-like


async def parse_upload_form(
    headers: Headers,
    stream: AsyncGenerator[bytes, None],
    spool_dir: Path,
    *,
    max_files: int,
    max_fields: int,
    max_part_size: int,
) -> FormData:
    """
    Purpose: parse a multipart body. Callers must close the returned form (deletes spools).
    Raises MultiPartException on malformed input or exceeded limits.
    """
    parser = SpoolDirMultiPartParser(
        headers,
        stream,
        spool_dir,
        max_files=max_files,
        max_fields=max_fields,
        max_part_size=max_part_size,
    )
    return await parser.parse()


def clean_orphaned_spool_files(
    spool_dir: Path | str,
    max_age_seconds: int = 3600,
    *,
    now: float | None = None,
) -> int:
    """
    Purpose: delete files in the upload spool directory older than max_age_seconds.
        Touches only this folder: never follows symlinks, never deletes subfolders or
        anything outside it. Logs only the number of files removed (no names).
    Inputs: spool_dir path, max_age_seconds (int), optional current timestamp (tests).
    Outputs: number of files deleted (int).
    Side effects: deletes orphaned spool files; logs count of deleted files.
    """
    path = Path(spool_dir)
    if not path.is_dir() or path.is_symlink():
        return 0
    current_time = time.time() if now is None else now
    cutoff = current_time - max_age_seconds
    removed = 0
    with os.scandir(path) as it:
        for entry in it:
            try:
                if entry.is_symlink():
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
                if stat.st_mtime < cutoff:
                    os.unlink(entry.path)
                    removed += 1
            except OSError:
                continue
    if removed > 0:
        logger.info("cleaned %d orphaned upload spool file(s)", removed)
    return removed


__all__ = [
    "MultiPartException",
    "SpoolDirMultiPartParser",
    "clean_orphaned_spool_files",
    "parse_upload_form",
]
