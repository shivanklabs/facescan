"""Temporary face quality score until OFIQ is integrated.

Combines four signals, each mapped to [0, 1]:
- size: shorter box side between min_face_px (0) and good_face_px (1)
- sharpness: variance of the Laplacian of the aligned grey crop
- pose: yaw and pitch estimated from landmarks, plus roll from the eye line
- detector: YuNet confidence
The final value is a weighted geometric mean, so one poor signal pulls the score down.
"""

import math
from dataclasses import dataclass

import cv2

from facescan_ai.types import Face, ImageBGR, QualityScore


@dataclass(frozen=True, slots=True)
class BasicQualityConfig:
    """Calibration points for the temporary score."""

    min_face_px: float = 20.0
    good_face_px: float = 112.0
    sharp_laplacian_var: float = 250.0
    max_yaw_ratio: float = 0.6
    max_pitch_offset: float = 0.5
    max_roll_degrees: float = 45.0
    weight_size: float = 1.0
    weight_sharpness: float = 1.0
    weight_pose: float = 1.0
    weight_detector: float = 0.5


def _clamp01(value: float) -> float:
    """Purpose: clamp to [0, 1]; NaN becomes 0. Side effects: none."""
    if math.isnan(value):
        return 0.0
    return min(1.0, max(0.0, value))


def pose_components(face: Face) -> tuple[float, float, float]:
    """
    Purpose: rough head pose from 5 landmarks.
    Outputs: (yaw_ratio, pitch_offset, roll_degrees). yaw_ratio is the nose's horizontal offset
        from the eye midpoint divided by the eye distance (0 = frontal); pitch_offset compares
        the nose's vertical position between the eye line and the mouth line with the frontal
        template (0 = frontal). Side effects: none.
    """
    (rex, rey), (lex, ley), (nx, ny), (rmx, rmy), (lmx, lmy) = face.landmarks
    eye_dx, eye_dy = lex - rex, ley - rey
    eye_dist = math.hypot(eye_dx, eye_dy)
    if eye_dist < 1e-6:
        return 1.0, 1.0, 90.0
    roll = math.degrees(math.atan2(eye_dy, eye_dx))
    # Project the nose onto the eye axis to estimate yaw independent of roll.
    mid_x, mid_y = (rex + lex) / 2, (rey + ley) / 2
    yaw_ratio = ((nx - mid_x) * eye_dx + (ny - mid_y) * eye_dy) / (eye_dist * eye_dist)
    eye_y = mid_y
    mouth_y = (rmy + lmy) / 2
    span = mouth_y - eye_y
    if abs(span) < 1e-6:
        return abs(yaw_ratio), 1.0, abs(roll)
    nose_position = (ny - eye_y) / span  # frontal template: about 0.49
    pitch_offset = abs(nose_position - 0.49) / 0.49
    return abs(yaw_ratio), pitch_offset, abs(roll)


class BasicFaceQuality:
    """FaceQuality implementation based on size, sharpness, pose and detector score."""

    name = "basic_quality"

    def __init__(self, config: BasicQualityConfig | None = None) -> None:
        """Purpose: set calibration. Side effects: none."""
        self.config = config or BasicQualityConfig()

    def assess(self, face: Face, aligned: ImageBGR) -> QualityScore:
        """
        Purpose: score one face in [0, 1] with its components.
        Inputs: detection and aligned 112x112 BGR crop. Side effects: none.
        """
        cfg = self.config
        size = _clamp01(
            (face.bbox.short_side - cfg.min_face_px) / (cfg.good_face_px - cfg.min_face_px)
        )
        grey = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        laplacian_var = float(cv2.Laplacian(grey, cv2.CV_64F).var())
        sharpness = _clamp01(laplacian_var / cfg.sharp_laplacian_var)
        yaw, pitch, roll = pose_components(face)
        pose = (
            _clamp01(1 - yaw / cfg.max_yaw_ratio)
            * _clamp01(1 - pitch / cfg.max_pitch_offset)
            * _clamp01(1 - roll / cfg.max_roll_degrees)
        )
        detector = _clamp01(face.score)
        parts = {
            "size": (size, cfg.weight_size),
            "sharpness": (sharpness, cfg.weight_sharpness),
            "pose": (pose, cfg.weight_pose),
            "detector": (detector, cfg.weight_detector),
        }
        total_weight = sum(w for _, w in parts.values())
        log_sum = sum(w * math.log(max(v, 1e-6)) for v, w in parts.values())
        value = _clamp01(math.exp(log_sum / total_weight))
        if min(v for v, _ in parts.values()) <= 0.0:
            value = 0.0
        return QualityScore(
            value=round(value, 4),
            model_name=self.name,
            components={k: round(v, 4) for k, (v, _) in parts.items()},
        )
