from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from app.connectors.photo_storage.local_filesystem_storage import (
    InvalidSignatureError,
    LocalFilesystemStorage,
)
from app.interfaces.photo_storage import (
    InvalidObjectReferenceError,
    ObjectNotFoundError,
    ObjectStorage,
)

BUCKET = "facescan-photos"
KEY = b"k" * 32


def make_storage(root: Path, minutes: int = 5) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(
        root=root,
        buckets=frozenset({BUCKET, "facescan-derivatives"}),
        signing_key=KEY,
        url_expiry_minutes=minutes,
        public_base_url="http://localhost:8000/",
    )


def url_parts(url: str) -> tuple[str, UUID, str, int, str]:
    parts = urlsplit(url)
    _, _, bucket, event_id, key = parts.path.split("/")
    query = parse_qs(parts.query)
    return bucket, UUID(event_id), key, int(query["expires"][0]), query["signature"][0]


def test_satisfies_port(tmp_path: Path) -> None:
    storage: ObjectStorage = make_storage(tmp_path)
    assert storage.capabilities.adapter_name == "local_filesystem"


def test_put_get_delete_roundtrip(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    event_id = uuid4()
    key = storage.put_object(BUCKET, event_id, b"jpeg-bytes", "image/jpeg")
    assert key.endswith(".jpg")
    assert (tmp_path / BUCKET / str(event_id) / key).read_bytes() == b"jpeg-bytes"
    assert storage.get_object(BUCKET, event_id, key) == b"jpeg-bytes"
    storage.delete_object(BUCKET, event_id, key)
    with pytest.raises(ObjectNotFoundError):
        storage.get_object(BUCKET, event_id, key)


def test_keys_are_random(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    event_id = uuid4()
    keys = {storage.put_object(BUCKET, event_id, b"x", "image/png") for _ in range(20)}
    assert len(keys) == 20


def test_objects_are_scoped_to_event(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    key = storage.put_object(BUCKET, uuid4(), b"x", "image/png")
    with pytest.raises(ObjectNotFoundError):
        storage.get_object(BUCKET, uuid4(), key)


def test_delete_event_objects(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    event_id, other = uuid4(), uuid4()
    for _ in range(3):
        storage.put_object(BUCKET, event_id, b"x", "image/webp")
    kept = storage.put_object(BUCKET, other, b"y", "image/webp")
    assert storage.delete_event_objects(BUCKET, event_id) == 3
    assert not (tmp_path / BUCKET / str(event_id)).exists()
    assert storage.get_object(BUCKET, other, kept) == b"y"
    assert storage.delete_event_objects(BUCKET, uuid4()) == 0


@pytest.mark.parametrize(
    ("bucket", "key"),
    [
        ("unknown-bucket", "a" * 43 + ".jpg"),
        ("../etc", "a" * 43 + ".jpg"),
        (BUCKET, "../../secret.jpg"),
        (BUCKET, "a" * 43 + ".exe"),
        (BUCKET, "short.jpg"),
    ],
)
def test_rejects_bad_references(tmp_path: Path, bucket: str, key: str) -> None:
    storage = make_storage(tmp_path)
    with pytest.raises(InvalidObjectReferenceError):
        storage.get_object(bucket, uuid4(), key)


def test_rejects_unsupported_content_type(tmp_path: Path) -> None:
    with pytest.raises(InvalidObjectReferenceError):
        make_storage(tmp_path).put_object(BUCKET, uuid4(), b"x", "text/html")


def test_rejects_short_signing_key_and_bad_expiry(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="signing key"):
        LocalFilesystemStorage(tmp_path, frozenset({BUCKET}), b"short", 5, "http://x")
    with pytest.raises(ValueError, match="expiry"):
        make_storage(tmp_path, minutes=0)
    with pytest.raises(ValueError, match="expiry"):
        make_storage(tmp_path, minutes=61)


def test_signed_url_roundtrip(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    event_id = uuid4()
    key = storage.put_object(BUCKET, event_id, b"x", "image/jpeg")
    signed = storage.create_signed_download_url(BUCKET, event_id, key)
    assert signed.url.startswith(f"http://localhost:8000/storage/{BUCKET}/{event_id}/{key}?")
    path = storage.verify_download(*url_parts(signed.url))
    assert path.read_bytes() == b"x"


def test_signed_url_for_missing_object_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ObjectNotFoundError):
        make_storage(tmp_path).create_signed_download_url(BUCKET, uuid4(), "a" * 43 + ".jpg")


def test_tampered_signature_is_rejected(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    event_id = uuid4()
    key = storage.put_object(BUCKET, event_id, b"x", "image/jpeg")
    other_key = storage.put_object(BUCKET, event_id, b"y", "image/jpeg")
    bucket, ev, k, expires, sig = url_parts(
        storage.create_signed_download_url(BUCKET, event_id, key).url
    )
    with pytest.raises(InvalidSignatureError):
        storage.verify_download(bucket, ev, other_key, expires, sig)
    with pytest.raises(InvalidSignatureError):
        storage.verify_download(bucket, uuid4(), k, expires, sig)
    with pytest.raises(InvalidSignatureError):
        storage.verify_download(bucket, ev, k, expires + 1, sig)
    with pytest.raises(InvalidSignatureError):
        storage.verify_download(bucket, ev, k, expires, "A" * 43)


def test_expired_url_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = make_storage(tmp_path, minutes=1)
    event_id = uuid4()
    key = storage.put_object(BUCKET, event_id, b"x", "image/jpeg")
    monkeypatch.setattr(LocalFilesystemStorage, "_now", staticmethod(lambda: 1_000_000.0))
    parts = url_parts(storage.create_signed_download_url(BUCKET, event_id, key).url)
    monkeypatch.setattr(LocalFilesystemStorage, "_now", staticmethod(lambda: 1_000_061.0))
    with pytest.raises(InvalidSignatureError):
        storage.verify_download(*parts)


def test_expiry_too_far_in_future_is_rejected(tmp_path: Path) -> None:
    storage = make_storage(tmp_path, minutes=1)
    event_id = uuid4()
    key = storage.put_object(BUCKET, event_id, b"x", "image/jpeg")
    bucket, ev, k, expires, sig = url_parts(
        storage.create_signed_download_url(BUCKET, event_id, key).url
    )
    with pytest.raises(InvalidSignatureError):
        storage.verify_download(bucket, ev, k, expires + 3600, sig)
