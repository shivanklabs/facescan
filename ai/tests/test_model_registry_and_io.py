import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest
from facescan_ai.image_io import ImageDecodeError, decode_image, list_images
from facescan_ai.model_registry import (
    ModelChecksumError,
    ModelFileMissingError,
    ModelRegistry,
    UnknownModelError,
    UnverifiedModelError,
)
from PIL import Image


def write_lock(folder: Path, entries: list[dict[str, str]]) -> None:
    (folder / "models.lock.json").write_text(json.dumps({"models": entries}), encoding="utf-8")


def entry(name: str, file: str, sha: str, url: str = "https://example.invalid/m.onnx") -> dict:
    return {
        "name": name,
        "version": "1",
        "file": file,
        "sha256": sha,
        "source_url": url,
        "licence": "MIT",
    }


def test_registry_verifies_checksums(tmp_path: Path) -> None:
    data = b"fake model bytes"
    good_sha = hashlib.sha256(data).hexdigest()
    (tmp_path / "good.onnx").write_bytes(data)
    (tmp_path / "bad.onnx").write_bytes(b"tampered")
    write_lock(
        tmp_path,
        [
            entry("good", "good.onnx", good_sha),
            entry("bad", "bad.onnx", good_sha),
            entry("missing", "missing.onnx", good_sha),
            entry("todo", "todo.onnx", "TODO", "TODO: later"),
        ],
    )
    registry = ModelRegistry(tmp_path)
    spec, loaded = registry.read_verified_bytes("good")
    assert loaded == data and spec.name == "good"
    with pytest.raises(ModelChecksumError):
        registry.read_verified_bytes("bad")
    with pytest.raises(ModelFileMissingError):
        registry.read_verified_bytes("missing")
    with pytest.raises(UnverifiedModelError):
        registry.read_verified_bytes("todo")
    with pytest.raises(UnknownModelError):
        registry.read_verified_bytes("buffalo_l")


def test_real_lock_marks_liveness_pinned_and_ofiq_unverified() -> None:
    """
    Purpose: verify that minifasnet and siglip2 models are pinned while ofiq remains unverified.
    Inputs: none. Outputs: none. Side effects: none.
    """
    registry = ModelRegistry()
    assert {n for n, s in registry.specs.items() if s.is_pinned} == {
        "yunet",
        "sface",
        "auraface",
        "minifasnet_v2",
        "minifasnet_v1se",
        "siglip2_image_encoder",
        "siglip2_text_encoder",
        "siglip2_tokenizer",
    }
    with pytest.raises(UnverifiedModelError):
        registry.spec("ofiq")


def encode(img: Image.Image, fmt: str = "PNG", **kwargs: object) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def test_decode_returns_bgr() -> None:
    img = Image.new("RGB", (4, 2), (255, 0, 0))  # pure red
    out = decode_image(encode(img))
    assert out.shape == (2, 4, 3) and out.dtype == np.uint8
    assert out[0, 0].tolist() == [0, 0, 255]


def test_decode_applies_exif_orientation() -> None:
    img = Image.new("RGB", (40, 20), (0, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise for display
    out = decode_image(encode(img, "JPEG", exif=exif))
    assert out.shape[:2] == (40, 20)


def test_decode_rejects_garbage() -> None:
    with pytest.raises(ImageDecodeError):
        decode_image(b"definitely not an image")


def test_list_images_filters_and_sorts(tmp_path: Path) -> None:
    for name in ("b.JPG", "a.png", "notes.txt", "sub/c.webp", "sub/d.heic"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    rel = [p.relative_to(tmp_path).as_posix() for p in list_images(tmp_path)]
    assert rel == ["a.png", "b.JPG", "sub/c.webp"]
    assert [p.name for p in list_images(tmp_path, recursive=False)] == ["a.png", "b.JPG"]
