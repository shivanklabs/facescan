"""Core value types shared by every facescan_ai component.

Biometric values (embeddings) are numpy arrays that never appear in repr, logs or files other
than the encrypted index.
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float32]
ImageBGR = NDArray[np.uint8]  # H x W x 3, BGR, as used by OpenCV


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned face box in pixel coordinates of the original image."""

    x: float
    y: float
    width: float
    height: float

    @property
    def x2(self) -> float:
        """Right edge."""
        return self.x + self.width

    @property
    def y2(self) -> float:
        """Bottom edge."""
        return self.y + self.height

    @property
    def short_side(self) -> float:
        """Smaller of width and height (used for minimum face size)."""
        return min(self.width, self.height)


Landmarks = tuple[
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
]
"""Five points in image order: right eye, left eye, nose tip, right and left mouth corner
(the subject's right eye appears on the image's left)."""


@dataclass(frozen=True, slots=True)
class Face:
    """A detected face: box, 5 landmarks and detector score in [0, 1]."""

    bbox: BBox
    landmarks: Landmarks
    score: float


@dataclass(frozen=True, slots=True, eq=False)
class Embedding:
    """An L2-normalised face embedding. Biometric data: never log, never return from an API."""

    vector: FloatArray
    engine: str
    engine_version: str

    @property
    def dim(self) -> int:
        """Vector length."""
        return int(self.vector.shape[0])

    def __repr__(self) -> str:
        """Purpose: debug text without any vector values."""
        return f"Embedding(engine={self.engine}@{self.engine_version}, dim={self.dim})"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class QualityScore:
    """Face quality in [0, 1], the assessor that produced it, and its component scores."""

    value: float
    model_name: str
    components: dict[str, float] = field(default_factory=dict)
