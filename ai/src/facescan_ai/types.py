"""Core value types shared by every facescan_ai component.

TODO: switch vector storage to numpy arrays once embedding adapters exist.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned face box in pixel coordinates."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class Face:
    """A detected face: box, 5 landmarks (eyes, nose, mouth corners) and detector score."""

    bbox: BBox
    landmarks: tuple[tuple[float, float], ...]
    score: float


@dataclass(frozen=True, slots=True)
class Embedding:
    """A face embedding. Biometric data: never log, never return from an API."""

    values: tuple[float, ...]
    model_name: str
    model_version: str

    def __repr__(self) -> str:
        return f"Embedding(model={self.model_name}@{self.model_version}, dim={len(self.values)})"


@dataclass(frozen=True, slots=True)
class QualityScore:
    """Face quality score in [0, 1] plus the model that produced it."""

    value: float
    model_name: str
