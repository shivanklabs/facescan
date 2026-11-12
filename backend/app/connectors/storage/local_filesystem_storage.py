"""ObjectStorage adapter for the local filesystem (development and single-server installs).

Layout: <root>/<bucket>/<event_id>/<random key>.<ext>, where root defaults to var/storage
(git-ignored). Object keys are random (no user input in paths). Downloads go through the API:
`create_signed_download_url` returns an HMAC-signed URL with a short expiry, which
`verify_download` checks before the file is served (see local_filesystem_download.py).
"""

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import UUID

from app.interfaces.capabilities import Capabilities
from app.interfaces.object_storage import (
    InvalidObjectReferenceError,
    ObjectNotFoundError,
    SignedUrl,
)

BUCKET_PATTERN = r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"
OBJECT_KEY_PATTERN = r"^[A-Za-z0-9_-]{43}\.(jpg|png|webp)$"
SIGNATURE_PATTERN = r"^[A-Za-z0-9_-]{43}$"
DOWNLOAD_ROUTE_PREFIX = "/storage"
MIN_SIGNING_KEY_BYTES = 32
MAX_EXPIRY_MINUTES = 60

_BUCKET_RE = re.compile(BUCKET_PATTERN)
_OBJECT_KEY_RE = re.compile(OBJECT_KEY_PATTERN)
_SIGNATURE_RE = re.compile(SIGNATURE_PATTERN)

CONTENT_TYPE_TO_EXTENSION: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
EXTENSION_TO_CONTENT_TYPE: dict[str, str] = {v: k for k, v in CONTENT_TYPE_TO_EXTENSION.items()}


class InvalidSignatureError(Exception):
    """The signed URL is malformed, tampered with or expired."""


class LocalFilesystemStorage:
    """Event-scoped private object storage on the local disk."""

    def __init__(
        self,
        root: Path,
        buckets: frozenset[str],
        signing_key: bytes,
        url_expiry_minutes: int,
        public_base_url: str,
    ) -> None:
        """
        Purpose: configure the adapter.
        Inputs: storage root, allowed bucket names, HMAC key (>= 32 bytes), URL expiry in
            minutes (1..60), base URL of the API that serves downloads.
        Outputs: none. Side effects: none (directories are created on first write).
        """
        if len(signing_key) < MIN_SIGNING_KEY_BYTES:
            raise ValueError("storage signing key must be at least 32 bytes")
        if not 1 <= url_expiry_minutes <= MAX_EXPIRY_MINUTES:
            raise ValueError("url expiry must be between 1 and 60 minutes")
        for bucket in buckets:
            _check_bucket_name(bucket)
        self._root = root.resolve()
        self._buckets = buckets
        self._signing_key = signing_key
        self._expiry_seconds = url_expiry_minutes * 60
        self._base_url = public_base_url.rstrip("/")

    @property
    def capabilities(self) -> Capabilities:
        """Static description: free, keeps data on this host."""
        return Capabilities(adapter_name="local_filesystem")

    def put_object(self, bucket: str, event_id: UUID, data: bytes, content_type: str) -> str:
        """
        Purpose: write bytes under a new random key.
        Inputs: bucket, event id, data, content type (jpeg/png/webp only).
        Outputs: object key. Side effects: creates directories and one file (never overwrites).
        """
        extension = CONTENT_TYPE_TO_EXTENSION.get(content_type)
        if extension is None:
            raise InvalidObjectReferenceError("unsupported content type")
        directory = self._event_dir(bucket, event_id)
        directory.mkdir(parents=True, exist_ok=True)
        object_key = f"{secrets.token_urlsafe(32)}.{extension}"
        with (directory / object_key).open("xb") as fh:
            fh.write(data)
        return object_key

    def get_object(self, bucket: str, event_id: UUID, object_key: str) -> bytes:
        """Purpose: read an object. Outputs: bytes. Raises ObjectNotFoundError if missing."""
        path = self.object_path(bucket, event_id, object_key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError from exc

    def delete_object(self, bucket: str, event_id: UUID, object_key: str) -> None:
        """Purpose: delete one object; missing is not an error. Side effects: deletes a file."""
        self.object_path(bucket, event_id, object_key).unlink(missing_ok=True)

    def delete_event_objects(self, bucket: str, event_id: UUID) -> int:
        """
        Purpose: delete all objects of one event in one bucket.
        Outputs: number of files deleted. Side effects: deletes files and the event directory.
        """
        directory = self._event_dir(bucket, event_id)
        if not directory.is_dir():
            return 0
        deleted = 0
        for path in directory.iterdir():
            if path.is_file() and _OBJECT_KEY_RE.fullmatch(path.name):
                path.unlink()
                deleted += 1
        if not any(directory.iterdir()):
            directory.rmdir()
        return deleted

    def create_signed_download_url(self, bucket: str, event_id: UUID, object_key: str) -> SignedUrl:
        """
        Purpose: issue a short-lived signed URL served by the API.
        Callers must have run the permission check first (invariant 6).
        Outputs: SignedUrl. Side effects: none. Raises ObjectNotFoundError if missing.
        """
        if not self.object_path(bucket, event_id, object_key).is_file():
            raise ObjectNotFoundError
        expires = int(self._now()) + self._expiry_seconds
        signature = self._sign(bucket, event_id, object_key, expires)
        path = "/".join(
            quote(part, safe="")
            for part in (DOWNLOAD_ROUTE_PREFIX.strip("/"), bucket, str(event_id), object_key)
        )
        query = urlencode({"expires": expires, "signature": signature})
        return SignedUrl(
            url=f"{self._base_url}/{path}?{query}",
            expires_at=datetime.fromtimestamp(expires, tz=UTC),
        )

    def verify_download(
        self, bucket: str, event_id: UUID, object_key: str, expires: int, signature: str
    ) -> Path:
        """
        Purpose: check a signed URL before serving a file.
        Inputs: the URL's path parts, expiry (unix seconds) and signature.
        Outputs: the file path. Side effects: none.
        Raises InvalidSignatureError (bad, tampered or expired) or ObjectNotFoundError.
        """
        if not _SIGNATURE_RE.fullmatch(signature):
            raise InvalidSignatureError
        now = int(self._now())
        if expires < now or expires > now + self._expiry_seconds:
            raise InvalidSignatureError
        try:
            expected = self._sign(bucket, event_id, object_key, expires)
        except InvalidObjectReferenceError as exc:
            raise InvalidSignatureError from exc
        if not hmac.compare_digest(expected, signature):
            raise InvalidSignatureError
        path = self.object_path(bucket, event_id, object_key)
        if not path.is_file():
            raise ObjectNotFoundError
        return path

    def object_path(self, bucket: str, event_id: UUID, object_key: str) -> Path:
        """
        Purpose: map a validated reference to a path inside the root.
        Outputs: path. Side effects: none. Raises InvalidObjectReferenceError on bad input.
        """
        if not _OBJECT_KEY_RE.fullmatch(object_key):
            raise InvalidObjectReferenceError("invalid object key")
        path = self._event_dir(bucket, event_id) / object_key
        if not path.resolve().is_relative_to(self._root):
            raise InvalidObjectReferenceError("path escapes storage root")
        return path

    def _event_dir(self, bucket: str, event_id: UUID) -> Path:
        """Purpose: <root>/<bucket>/<event_id>. Raises on unknown bucket. Side effects: none."""
        _check_bucket_name(bucket)
        if bucket not in self._buckets:
            raise InvalidObjectReferenceError("unknown bucket")
        return self._root / bucket / str(event_id)

    def _sign(self, bucket: str, event_id: UUID, object_key: str, expires: int) -> str:
        """Purpose: HMAC-SHA256 over the reference and expiry. Outputs: base64url, no padding."""
        self.object_path(bucket, event_id, object_key)  # validates every part
        message = f"v1\n{bucket}\n{event_id}\n{object_key}\n{expires}".encode()
        digest = hmac.new(self._signing_key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def _now() -> float:
        """Purpose: current unix time (patched in tests). Side effects: none."""
        return time.time()


def _check_bucket_name(bucket: str) -> None:
    """Purpose: reject bucket names that are not S3-style lowercase names."""
    if not _BUCKET_RE.fullmatch(bucket):
        raise InvalidObjectReferenceError("invalid bucket name")
