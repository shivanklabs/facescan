"""Per-event calibration of similarity thresholds from impostor pairs.

Two faces in the same photo are different people (cannot-link), so their similarity scores
form an impostor distribution specific to this event's cameras, light and crowd. Each engine's
release and borderline thresholds are set at target false-match rates (FMR) on that
distribution. Same-photo impostors look more alike than random strangers (same light and
camera), so the estimate errs on the strict side.

An empirical quantile at FMR f is only meaningful with about 3/f pairs or more. With fewer
pairs the conservative default threshold is used and the result says so. Calibrated values
never go below a configured floor.
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from facescan_ai.types import FloatArray


@dataclass(frozen=True, slots=True)
class EngineThresholdDefaults:
    """Conservative fallback thresholds and hard floors for one engine (cosine similarity)."""

    release: float
    borderline: float
    release_floor: float
    borderline_floor: float


DEFAULT_THRESHOLDS: dict[str, EngineThresholdDefaults] = {
    # AuraFace (ArcFace-style, 512-d): strangers mostly score below 0.3.
    "auraface": EngineThresholdDefaults(
        release=0.50, borderline=0.40, release_floor=0.40, borderline_floor=0.30
    ),
    # SFace (128-d): OpenCV's recommended same-person threshold is 0.363.
    "sface": EngineThresholdDefaults(
        release=0.45, borderline=0.363, release_floor=0.363, borderline_floor=0.30
    ),
}


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """Target false-match rates and the pair count needed to trust an empirical quantile."""

    release_fmr: float = 0.001
    borderline_fmr: float = 0.01
    pairs_per_fmr_unit: float = 3.0  # need >= pairs_per_fmr_unit / fmr impostor pairs

    def __post_init__(self) -> None:
        """Purpose: reject invalid values. Side effects: none."""
        if not 0 < self.release_fmr <= self.borderline_fmr < 1:
            raise ValueError("need 0 < release_fmr <= borderline_fmr < 1")
        if self.pairs_per_fmr_unit <= 0:
            raise ValueError("pairs_per_fmr_unit must be positive")

    def min_pairs(self, fmr: float) -> int:
        """Impostor pairs needed to calibrate at this FMR."""
        return math.ceil(self.pairs_per_fmr_unit / fmr)


@dataclass(frozen=True, slots=True)
class EngineCalibration:
    """Thresholds for one engine and how they were obtained."""

    engine: str
    release_threshold: float
    borderline_threshold: float
    impostor_pairs: int
    release_calibrated: bool
    borderline_calibrated: bool
    impostor_max: float | None

    def describe(self) -> str:
        """Purpose: one line for console output (no biometric data)."""
        how_r = "calibrated" if self.release_calibrated else "DEFAULT (too few impostor pairs)"
        how_b = "calibrated" if self.borderline_calibrated else "DEFAULT (too few impostor pairs)"
        return (
            f"{self.engine}: release {self.release_threshold:.3f} [{how_r}], "
            f"borderline {self.borderline_threshold:.3f} [{how_b}], "
            f"impostor pairs {self.impostor_pairs}"
        )


def same_photo_pairs(photo_ids: list[str], usable: list[bool]) -> list[tuple[int, int]]:
    """
    Purpose: all pairs of usable faces that share a photo (known impostors).
    Inputs: photo id per face, usable flag per face (e.g. not weak).
    Outputs: index pairs (i < j). Side effects: none.
    """
    by_photo: dict[str, list[int]] = {}
    for i, (photo, ok) in enumerate(zip(photo_ids, usable, strict=True)):
        if ok:
            by_photo.setdefault(photo, []).append(i)
    pairs = []
    for faces in by_photo.values():
        for a in range(len(faces)):
            for b in range(a + 1, len(faces)):
                pairs.append((faces[a], faces[b]))
    return pairs


def impostor_scores(vectors: NDArray[np.float32], pairs: list[tuple[int, int]]) -> FloatArray:
    """Purpose: cosine similarity of each pair. Outputs: scores. Side effects: none."""
    if not pairs:
        return np.zeros((0,), dtype=np.float32)
    idx = np.array(pairs)
    a = vectors[idx[:, 0]]
    b = vectors[idx[:, 1]]
    a = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
    return np.asarray(np.clip((a * b).sum(axis=1), -1.0, 1.0), dtype=np.float32)


def calibrate_engine(
    engine: str, scores: FloatArray, config: CalibrationConfig
) -> EngineCalibration:
    """
    Purpose: thresholds for one engine from its impostor scores.
    Inputs: engine name (must have defaults), impostor cosine scores, config.
    Outputs: EngineCalibration. Side effects: none.
    """
    defaults = DEFAULT_THRESHOLDS[engine]
    n = int(scores.shape[0])

    def threshold(fmr: float, default: float, floor: float) -> tuple[float, bool]:
        if n < config.min_pairs(fmr):
            return default, False
        value = float(np.quantile(scores, 1.0 - fmr, method="higher"))
        return max(value, floor), True

    release, release_ok = threshold(config.release_fmr, defaults.release, defaults.release_floor)
    borderline, borderline_ok = threshold(
        config.borderline_fmr, defaults.borderline, defaults.borderline_floor
    )
    borderline = min(borderline, release)
    return EngineCalibration(
        engine=engine,
        release_threshold=round(release, 4),
        borderline_threshold=round(borderline, 4),
        impostor_pairs=n,
        release_calibrated=release_ok,
        borderline_calibrated=borderline_ok,
        impostor_max=round(float(scores.max()), 4) if n else None,
    )


def calibrate(
    vectors_by_engine: dict[str, NDArray[np.float32]],
    photo_ids: list[str],
    usable: list[bool],
    config: CalibrationConfig,
) -> dict[str, EngineCalibration]:
    """
    Purpose: calibrate every engine from same-photo impostor pairs of usable faces.
    Inputs: N x D matrix per engine, photo id and usable flag per face, config.
    Outputs: engine -> EngineCalibration. Side effects: none.
    """
    pairs = same_photo_pairs(photo_ids, usable)
    return {
        engine: calibrate_engine(engine, impostor_scores(vectors, pairs), config)
        for engine, vectors in vectors_by_engine.items()
    }
