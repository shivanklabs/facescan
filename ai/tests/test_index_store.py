import base64
import secrets
from pathlib import Path

import numpy as np
import pytest
from facescan_ai.encrypted_face_index import (
    MAGIC,
    EventIndex,
    IndexDecryptionError,
    IndexedFace,
    IndexKeyError,
    IndexNotFoundError,
    decrypt_index,
    delete_index,
    encrypt_index,
    load_index,
    load_key,
    save_index,
    validate_event_name,
)
from facescan_ai.types import BBox


def make_index(event: str = "wedding-2026") -> EventIndex:
    rng = np.random.default_rng(7)
    faces = [
        IndexedFace(
            photo_id=f"day1/IMG_{i}.jpg",
            bbox=BBox(10.5 * i, 20, 64, 80),
            landmarks=((1, 2), (3, 4), (5, 6), (7, 8), (9, 10)),
            detector_score=0.9,
            quality=0.75,
            embeddings={
                "auraface": rng.normal(size=512).astype(np.float32),
                "sface": rng.normal(size=128).astype(np.float32),
            },
        )
        for i in range(3)
    ]
    return EventIndex(
        event=event,
        engine_versions={"auraface": "v1", "sface": "2021dec"},
        photos=[f.photo_id for f in faces] + ["day1/empty.jpg"],
        faces=faces,
    )


def key() -> bytes:
    return secrets.token_bytes(32)


def test_encryption_round_trip_keeps_everything() -> None:
    k = key()
    original = make_index()
    restored = decrypt_index(encrypt_index(original, k), original.event, k)
    assert restored.event == original.event
    assert restored.engine_versions == original.engine_versions
    assert restored.photos == original.photos
    assert restored.created_at == original.created_at
    for a, b in zip(original.faces, restored.faces, strict=True):
        assert (a.photo_id, a.bbox, a.landmarks, a.quality) == (
            b.photo_id,
            b.bbox,
            b.landmarks,
            b.quality,
        )
        for engine in a.embeddings:
            assert np.array_equal(a.embeddings[engine], b.embeddings[engine])
            assert b.embeddings[engine].dtype == np.float32


def test_wrong_key_fails() -> None:
    blob = encrypt_index(make_index(), key())
    with pytest.raises(IndexDecryptionError):
        decrypt_index(blob, "wedding-2026", key())


def test_file_renamed_to_another_event_fails() -> None:
    k = key()
    blob = encrypt_index(make_index("event-a"), k)
    with pytest.raises(IndexDecryptionError):
        decrypt_index(blob, "event-b", k)


def test_tampering_fails() -> None:
    k = key()
    blob = bytearray(encrypt_index(make_index(), k))
    blob[-5] ^= 0x01
    with pytest.raises(IndexDecryptionError):
        decrypt_index(bytes(blob), "wedding-2026", k)
    with pytest.raises(IndexDecryptionError):
        decrypt_index(b"not an index", "wedding-2026", k)


def test_ciphertext_contains_no_plaintext() -> None:
    index = make_index()
    blob = encrypt_index(index, key())
    assert blob.startswith(MAGIC)
    vector_bytes = index.faces[0].embeddings["auraface"].astype("<f4").tobytes()[:16]
    assert vector_bytes not in blob
    assert b"day1/IMG_0.jpg" not in blob
    assert b"auraface" not in blob
    # Two encryptions of the same index differ (random nonce).
    assert encrypt_index(index, key()) != encrypt_index(index, key())


def test_save_load_delete(tmp_path: Path) -> None:
    k = key()
    path = save_index(make_index(), k, tmp_path)
    assert path == tmp_path / "wedding-2026.fsidx"
    assert [p.name for p in tmp_path.iterdir()] == ["wedding-2026.fsidx"]  # no temp left
    assert len(load_index("wedding-2026", k, tmp_path).faces) == 3
    assert delete_index("wedding-2026", tmp_path)
    assert not delete_index("wedding-2026", tmp_path)
    with pytest.raises(IndexNotFoundError):
        load_index("wedding-2026", k, tmp_path)


@pytest.mark.parametrize("name", ["", "../x", "UPPER", "a b", "-start", "x" * 65, "a/b", "a.b"])
def test_bad_event_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        validate_event_name(name)


def test_good_event_names() -> None:
    for name in ("a", "wedding-2026", "conf_day_2", "x" * 64):
        assert validate_event_name(name) == name


def test_load_key() -> None:
    raw = secrets.token_bytes(32)
    good = base64.b64encode(raw).decode()
    assert load_key({"FACESCAN_BENCH_KEY": good}) == raw
    for env in (
        {},
        {"FACESCAN_BENCH_KEY": ""},
        {"FACESCAN_BENCH_KEY": "CHANGE_ME_BASE64_32_BYTES"},
        {"FACESCAN_BENCH_KEY": base64.b64encode(b"short").decode()},
    ):
        with pytest.raises(IndexKeyError) as info:
            load_key(env)
        assert good not in str(info.value)


def test_repr_hides_vectors() -> None:
    face = make_index().faces[0]
    first_value = f"{float(face.embeddings['auraface'][0]):.4f}"
    assert first_value not in repr(face)
