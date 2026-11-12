"""Tunable settings for the offline face pipeline. Defaults favour precision (invariant 8)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """Face detection settings.

    min_face_px: faces whose shorter box side is smaller (original pixels) are dropped.
    score_threshold: minimum detector confidence in [0, 1].
    detector_min_px: face size (at a pyramid level) the detector finds reliably; the finest
        pyramid level is chosen so that min_face_px maps to at least this size.
    tile_size: network input size (YuNet 2023mar: 640).
    tile_overlap: overlap between neighbouring tiles, in pixels at that level.
    nms_iou: IoU above which overlapping detections are merged.
    edge_margin: detections this close to an inner tile edge are dropped (cut-off faces).
    """

    min_face_px: float = 20.0
    score_threshold: float = 0.7
    detector_min_px: float = 16.0
    tile_size: int = 640
    tile_overlap: int = 192
    nms_iou: float = 0.35
    edge_margin: float = 2.0

    def __post_init__(self) -> None:
        """Purpose: reject invalid values early. Side effects: none."""
        if self.min_face_px <= 0 or self.detector_min_px <= 0:
            raise ValueError("face sizes must be positive")
        if not 0.0 <= self.score_threshold <= 1.0:
            raise ValueError("score_threshold must be in [0, 1]")
        if not 0.0 < self.nms_iou < 1.0:
            raise ValueError("nms_iou must be in (0, 1)")
        if not 0 <= self.tile_overlap < self.tile_size:
            raise ValueError("tile_overlap must be in [0, tile_size)")

    @property
    def start_scale(self) -> float:
        """Finest pyramid scale needed so the smallest wanted face is detectable."""
        return min(1.0, self.detector_min_px / self.min_face_px)


# Blurring errs towards blurring: lower threshold, smaller faces (privacy: "when unsure, blur").
BLUR_DETECTOR_CONFIG = DetectorConfig(min_face_px=8.0, score_threshold=0.3, detector_min_px=12.0)
