"""HTTP route that serves files of the local filesystem storage adapter via signed URLs.

Only mounted when FACESCAN_STORAGE_SIGNING_KEY is set. The permission check happens before a
URL is signed (in the module that issues it); this route only checks the signature and expiry.
"""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from app.adapters.storage.local_filesystem_storage import (
    BUCKET_PATTERN,
    DOWNLOAD_ROUTE_PREFIX,
    EXTENSION_TO_CONTENT_TYPE,
    OBJECT_KEY_PATTERN,
    SIGNATURE_PATTERN,
    InvalidSignatureError,
    LocalFilesystemStorage,
)
from app.core.config import Settings
from app.ports.object_storage import ObjectNotFoundError

DOWNLOAD_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def build_local_storage(
    settings: Settings, repo_root: Path | None = None
) -> LocalFilesystemStorage:
    """
    Purpose: create the adapter from settings.
    Inputs: settings (signing key must be set); optional base dir for a relative storage root
        (defaults to the current working directory).
    Outputs: LocalFilesystemStorage. Side effects: none. Raises ValueError if the key is unset.
    """
    if settings.storage_signing_key is None:
        raise ValueError("FACESCAN_STORAGE_SIGNING_KEY is not set")
    root = Path(settings.storage_root)
    if not root.is_absolute() and repo_root is not None:
        root = repo_root / root
    return LocalFilesystemStorage(
        root=root,
        buckets=frozenset(
            {
                settings.storage_bucket_photos,
                settings.storage_bucket_derivatives,
                settings.storage_bucket_quarantine,
            }
        ),
        signing_key=settings.storage_signing_key.get_secret_value().encode("utf-8"),
        url_expiry_minutes=settings.storage_url_expiry_minutes,
        public_base_url=settings.storage_public_base_url,
    )


def create_download_router(storage: LocalFilesystemStorage) -> APIRouter:
    """
    Purpose: build the signed-download router bound to one adapter instance.
    Inputs: storage adapter. Outputs: APIRouter. Side effects: none.
    """
    router = APIRouter(prefix=DOWNLOAD_ROUTE_PREFIX, tags=["storage"])

    def get_storage() -> LocalFilesystemStorage:
        """Purpose: dependency returning the bound adapter. Side effects: none."""
        return storage

    @router.get("/{bucket}/{event_id}/{object_key}", response_class=FileResponse)
    def download(
        bucket: Annotated[str, PathParam(pattern=BUCKET_PATTERN)],
        event_id: UUID,
        object_key: Annotated[str, PathParam(pattern=OBJECT_KEY_PATTERN)],
        expires: Annotated[int, Query(ge=0, le=2**40)],
        signature: Annotated[str, Query(pattern=SIGNATURE_PATTERN)],
        adapter: Annotated[LocalFilesystemStorage, Depends(get_storage)],
    ) -> FileResponse:
        """
        Purpose: serve one file if the signed URL is valid and unexpired.
        Inputs: validated path and query parameters.
        Outputs: the file (403 on a bad or expired signature, 404 if missing).
        Side effects: reads the file.
        """
        try:
            path = adapter.verify_download(bucket, event_id, object_key, expires, signature)
        except InvalidSignatureError as exc:
            raise HTTPException(status_code=403, detail="invalid_or_expired_link") from exc
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="not_found") from exc
        extension = object_key.rsplit(".", 1)[1]
        return FileResponse(
            path,
            media_type=EXTENSION_TO_CONTENT_TYPE[extension],
            headers=DOWNLOAD_HEADERS,
        )

    return router
