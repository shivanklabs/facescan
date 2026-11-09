"""Image decoding with Pillow: EXIF orientation applied, size limited, BGR numpy output.

Decoding happens from bytes in memory; nothing here writes files (selfies stay in memory,
security invariant 3).
"""

import io
import logging
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from facescan_ai.types import ImageBGR

SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_PIXELS = 120_000_000  # about 120 MP; larger files are refused (decompression bombs)

# Pillow logs every EXIF tag (with raw bytes, GPS pointers) at DEBUG level. Image data and
# metadata must never reach logs (invariant 7), so Pillow's loggers are capped at INFO even
# when an application enables DEBUG globally.
logging.getLogger("PIL").setLevel(logging.INFO)


class ImageDecodeError(Exception):
    """The bytes are not a supported, safe-to-decode image."""


def decode_image(data: bytes) -> ImageBGR:
    """
    Purpose: decode image bytes to an upright H x W x 3 uint8 BGR array.
    Inputs: encoded bytes (JPEG, PNG, WebP). Outputs: BGR array.
    Side effects: none. Raises ImageDecodeError on unsupported, corrupt or oversized input.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.width * img.height > MAX_PIXELS:
                    raise ImageDecodeError("image is too large")
                upright = ImageOps.exif_transpose(img)
                rgb = np.asarray(upright.convert("RGB"), dtype=np.uint8)
    except ImageDecodeError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageDecodeError(type(exc).__name__) from exc
    return np.ascontiguousarray(rgb[:, :, ::-1])


def read_image(path: Path) -> ImageBGR:
    """Purpose: read and decode an image file. Side effects: reads the file."""
    return decode_image(path.read_bytes())


def list_images(folder: Path, recursive: bool = True) -> list[Path]:
    """
    Purpose: supported image files in a folder, sorted for stable photo ids.
    Side effects: lists the directory.
    """
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
