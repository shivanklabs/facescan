"""Multipart parsing for event-photo uploads with a controlled spool folder.

Starlette keeps each file part in memory up to 1 MB and then rolls it over to a temporary
file in the system temp folder. For event photos we keep that behaviour but put the spool
files under FACESCAN_UPLOAD_SPOOL_DIR (default var/tmp/uploads, git-ignored); they are
deleted when the form is closed at the end of the request. Selfies never use multipart: they
are read as a raw body into memory (app/modules/matching/router.py).
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast

from starlette.datastructures import FormData, Headers
from starlette.formparsers import MultiPartException, MultiPartParser

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


__all__ = ["MultiPartException", "SpoolDirMultiPartParser", "parse_upload_form"]
