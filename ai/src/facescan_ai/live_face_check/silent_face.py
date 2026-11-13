"""Silent-Face MiniFASNet passive anti-spoofing (Apache-2.0) and challenge checks.

Ensemble of MiniFASNetV2 (scale 2.7) and MiniFASNetV1SE (scale 4.0) combined with
eye blink and head-turn challenge checks and frame quality gating (blur, lighting, margin).
"""

from dataclasses import dataclass, field

import cv2
import numpy as np
import onnxruntime as ort

from facescan_ai.model_loader import ModelRegistry
from facescan_ai.types import BBox, Face, ImageBGR

MINIFASNET_V2 = "minifasnet_v2"
MINIFASNET_V1SE = "minifasnet_v1se"


@dataclass(frozen=True, slots=True)
class SilentFaceConfig:
    """Thresholds for passive anti-spoofing and active challenges."""

    spoof_threshold: float = 0.50
    min_blur_var: float = 40.0
    min_luminance: float = 35.0
    max_luminance: float = 230.0
    min_face_size: int = 60
    margin_px: int = 5
    yaw_center_min: float = 0.38
    yaw_center_max: float = 0.62
    yaw_turn_delta: float = 0.08
    min_blink_eye_diff: float = 0.03


@dataclass(frozen=True, slots=True)
class FrameQualityResult:
    """Quality facts for one selfie frame."""

    passed: bool
    blur_score: float
    luminance: float
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LivenessResult:
    """Liveness evaluation result for a sequence of selfie frames."""

    passed: bool
    is_live: bool
    blink_detected: bool
    turn_detected: bool
    silent_face_score: float
    reasons: list[str] = field(default_factory=list)


def _crop_scaled_patch(image: ImageBGR, bbox: BBox, scale: float) -> ImageBGR:
    """
    Purpose: crop scaled bounding box centered on face matching upstream Silent-Face logic.
    Inputs: BGR image, face BBox, scale factor.
    Outputs: cropped BGR patch of size (80, 80).
    Side effects: none.
    """
    src_h, src_w = image.shape[:2]
    box_w = bbox.width
    box_h = bbox.height
    scale = min((src_h - 1) / max(1, box_h), min((src_w - 1) / max(1, box_w), scale))
    new_width = box_w * scale
    new_height = box_h * scale
    center_x = box_w / 2.0 + bbox.x
    center_y = box_h / 2.0 + bbox.y

    left_top_x = center_x - new_width / 2.0
    left_top_y = center_y - new_height / 2.0
    right_bottom_x = center_x + new_width / 2.0
    right_bottom_y = center_y + new_height / 2.0

    if left_top_x < 0:
        right_bottom_x -= left_top_x
        left_top_x = 0
    if left_top_y < 0:
        right_bottom_y -= left_top_y
        left_top_y = 0
    if right_bottom_x > src_w - 1:
        left_top_x -= right_bottom_x - src_w + 1
        right_bottom_x = src_w - 1
    if right_bottom_y > src_h - 1:
        left_top_y -= right_bottom_y - src_h + 1
        right_bottom_y = src_h - 1

    crop = image[
        int(left_top_y) : int(right_bottom_y) + 1, int(left_top_x) : int(right_bottom_x) + 1
    ]
    if crop.size == 0 or crop.shape[0] == 0 or crop.shape[1] == 0:
        return np.zeros((80, 80, 3), dtype=np.uint8)
    return np.asarray(cv2.resize(crop, (80, 80)), dtype=np.uint8)


class SilentFaceDetector:
    """MiniFASNet ensemble + challenge verification."""

    def __init__(
        self,
        registry: ModelRegistry,
        config: SilentFaceConfig | None = None,
        threads: int = 0,
    ) -> None:
        """
        Purpose: load and verify MiniFASNetV2 and MiniFASNetV1SE models.
        Inputs: ModelRegistry, optional config, ONNX thread count.
        Outputs: none.
        Side effects: loads model weights.
        """
        self.config = config or SilentFaceConfig()
        _, self._session_v2 = registry.create_session(MINIFASNET_V2, threads)
        _, self._session_v1se = registry.create_session(MINIFASNET_V1SE, threads)

    def _predict_model(
        self, image: ImageBGR, bbox: BBox, scale: float, session: ort.InferenceSession
    ) -> float:
        """
        Purpose: run inference on a scaled crop with one MiniFASNet model.
        Inputs: image, bbox, scale, session.
        Outputs: bonafide (real face) probability in [0, 1].
        Side effects: none.
        """
        patch = _crop_scaled_patch(image, bbox, scale)
        blob = patch.astype(np.float32).transpose((2, 0, 1))[np.newaxis, :]
        outputs = session.run(["output"], {"input": blob})[0]
        # Silent-Face output: 3 classes [spoof, real, spoof]. Class 1 is real.
        return float(outputs[0, 1])

    def score_face(self, image: ImageBGR, bbox: BBox) -> float:
        """
        Purpose: calculate combined passive anti-spoofing score from the ensemble.
        Inputs: BGR image, face BBox.
        Outputs: ensemble bonafide score in [0.0, 1.0].
        Side effects: none.
        """
        score_v2 = self._predict_model(image, bbox, scale=2.7, session=self._session_v2)
        score_v1se = self._predict_model(image, bbox, scale=4.0, session=self._session_v1se)
        return (score_v2 + score_v1se) / 2.0

    def check_frame_quality(self, image: ImageBGR, face: Face) -> FrameQualityResult:
        """
        Purpose: verify image quality (blur, lighting, margins, min size).
        Inputs: BGR image, detected Face.
        Outputs: FrameQualityResult.
        Side effects: none.
        """
        img_h, img_w = image.shape[:2]
        bbox = face.bbox
        cfg = self.config

        if bbox.short_side < cfg.min_face_size:
            return FrameQualityResult(
                passed=False, blur_score=0.0, luminance=0.0, reason="face_too_small"
            )

        if (
            bbox.x < cfg.margin_px
            or bbox.y < cfg.margin_px
            or bbox.x2 > img_w - cfg.margin_px
            or bbox.y2 > img_h - cfg.margin_px
        ):
            return FrameQualityResult(
                passed=False, blur_score=0.0, luminance=0.0, reason="face_touches_margin"
            )

        x1 = max(0, int(bbox.x))
        y1 = max(0, int(bbox.y))
        x2 = min(img_w, int(bbox.x2))
        y2 = min(img_h, int(bbox.y2))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return FrameQualityResult(
                passed=False, blur_score=0.0, luminance=0.0, reason="invalid_crop"
            )

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        lum = float(gray.mean())

        if laplacian_var < cfg.min_blur_var:
            return FrameQualityResult(
                passed=False, blur_score=laplacian_var, luminance=lum, reason="blurry_image"
            )
        if lum < cfg.min_luminance:
            return FrameQualityResult(
                passed=False, blur_score=laplacian_var, luminance=lum, reason="too_dark"
            )
        if lum > cfg.max_luminance:
            return FrameQualityResult(
                passed=False, blur_score=laplacian_var, luminance=lum, reason="overexposed"
            )

        return FrameQualityResult(passed=True, blur_score=laplacian_var, luminance=lum)

    def estimate_head_yaw(self, landmarks: tuple[tuple[float, float], ...]) -> tuple[float, str]:
        """
        Purpose: estimate head yaw ratio from 5 landmarks (right eye, left eye, nose tip).
        Inputs: 5 landmarks.
        Outputs: (yaw_ratio, pose_category: 'center' | 'left' | 'right').
        Side effects: none.
        """
        right_eye_x = landmarks[0][0]
        left_eye_x = landmarks[1][0]
        nose_x = landmarks[2][0]

        d_right = abs(nose_x - right_eye_x)
        d_left = abs(left_eye_x - nose_x)
        total = d_left + d_right
        if total < 1e-6:
            return (0.5, "center")

        ratio = d_left / total
        cfg = self.config
        if ratio < cfg.yaw_center_min:
            return (ratio, "left")
        if ratio > cfg.yaw_center_max:
            return (ratio, "right")
        return (ratio, "center")

    def check_blink(
        self,
        image_front: ImageBGR,
        face_front: Face,
        image_blink: ImageBGR,
        face_blink: Face,
    ) -> bool:
        """
        Purpose: verify eye blink by comparing eye regions between front and blink frames.
        Inputs: front image/face and blink image/face.
        Outputs: True if blink detected.
        Side effects: none.
        """

        def eye_patch(
            image: ImageBGR, pt: tuple[float, float], face_bbox: BBox
        ) -> np.ndarray | None:
            size = max(8, int(face_bbox.width * 0.14))
            px, py = int(round(pt[0])), int(round(pt[1]))
            x1 = max(0, px - size)
            x2 = min(image.shape[1], px + size)
            y1 = max(0, py - size)
            y2 = min(image.shape[0], py + size)
            patch = image[y1:y2, x1:x2]
            if patch.size == 0:
                return None
            return cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (24, 24))

        # Check subject right eye (landmarks[0]) and left eye (landmarks[1])
        diffs = []
        for idx in (0, 1):
            p1 = eye_patch(image_front, face_front.landmarks[idx], face_front.bbox)
            p2 = eye_patch(image_blink, face_blink.landmarks[idx], face_blink.bbox)
            if p1 is not None and p2 is not None:
                # Relative difference normalized by intensity range
                diff = float(np.mean(np.abs(p1.astype(np.float32) - p2.astype(np.float32)))) / 255.0
                diffs.append(diff)

        if not diffs:
            return False
        mean_diff = float(np.mean(diffs))
        return mean_diff >= self.config.min_blink_eye_diff

    def evaluate_frames(
        self,
        frames: list[tuple[ImageBGR, Face]],
        front_only: bool = False,
    ) -> LivenessResult:
        """
        Purpose: full liveness evaluation for guided scan or front-only fallback.
        Inputs: list of (ImageBGR, Face) pairs in pose order:
            Guided: [front, blink, left, right] (or [front, blink, right, left])
            Front-only: [front, blink..., front]; the blink may be in any middle frame, so a
                client can send a short burst taken while the person blinks.
        Outputs: LivenessResult.
        Side effects: none.
        """
        reasons: list[str] = []
        if len(frames) < (2 if front_only else 3):
            return LivenessResult(
                passed=False,
                is_live=False,
                blink_detected=False,
                turn_detected=False,
                silent_face_score=0.0,
                reasons=["insufficient_frames"],
            )

        # 1. Quality check on all frames
        scores = []
        for i, (img, face) in enumerate(frames):
            qual = self.check_frame_quality(img, face)
            if not qual.passed:
                reasons.append(f"frame_{i + 1}_{qual.reason}")
            scores.append(self.score_face(img, face.bbox))

        avg_score = float(np.mean(scores))
        is_live = avg_score >= self.config.spoof_threshold
        if not is_live:
            reasons.append("silent_face_spoof_detected")

        # 2. Blink check against the front frame (0). Guided: frame 1 is the blink frame.
        # Front-only: any middle frame (a burst taken while the person blinks).
        candidates = frames[1:-1] if front_only and len(frames) >= 3 else frames[1:2]
        blink_ok = any(
            self.check_blink(frames[0][0], frames[0][1], img, face) for img, face in candidates
        )
        if not blink_ok:
            reasons.append("blink_not_detected")

        # 3. Head turn challenge check
        turn_ok = True
        if not front_only:
            front_ratio, front_pose = self.estimate_head_yaw(frames[0][1].landmarks)
            # Find left and right turned frames among remaining frames
            found_left = False
            found_right = False
            for _img, face in frames[2:]:
                ratio, pose = self.estimate_head_yaw(face.landmarks)
                if ratio < front_ratio - self.config.yaw_turn_delta:
                    found_left = True
                elif ratio > front_ratio + self.config.yaw_turn_delta:
                    found_right = True

            turn_ok = found_left and found_right
            if not turn_ok:
                reasons.append("head_turn_not_completed")

        passed = is_live and blink_ok and turn_ok and (len(reasons) == 0)
        return LivenessResult(
            passed=passed,
            is_live=is_live,
            blink_detected=blink_ok,
            turn_detected=turn_ok,
            silent_face_score=avg_score,
            reasons=reasons,
        )
