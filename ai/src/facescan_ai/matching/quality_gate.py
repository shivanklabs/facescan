"""Quality gate: faces too small or too poor for reliable embeddings are marked weak.

Weak faces stay in the index, but they never form or anchor a cluster and never feed the
calibration. They can be released only through a strong cluster match plus the per-face check.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualityGateConfig:
    """Thresholds below which a face is weak."""

    min_face_px: float = 40.0
    min_quality: float = 0.45

    def __post_init__(self) -> None:
        """Purpose: reject invalid values. Side effects: none."""
        if self.min_face_px < 0 or not 0.0 <= self.min_quality <= 1.0:
            raise ValueError("min_face_px must be >= 0 and min_quality in [0, 1]")


def is_weak(face_short_side_px: float, quality: float, config: QualityGateConfig) -> bool:
    """
    Purpose: apply the gate to one face.
    Inputs: shorter box side in pixels, quality in [0, 1], gate config.
    Outputs: True if the face is weak. Side effects: none.
    """
    return face_short_side_px < config.min_face_px or quality < config.min_quality
