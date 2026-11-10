"""Unit tests for SilentFaceDetector (passive anti-spoofing and challenges)."""

import cv2
import numpy as np
import pytest
from facescan_ai.liveness.silent_face import (
    SilentFaceDetector,
    _crop_scaled_patch,
)
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.types import BBox, Face


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def detector(registry: ModelRegistry) -> SilentFaceDetector:
    return SilentFaceDetector(registry)


def test_crop_scaled_patch_pads_and_resizes() -> None:
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[20:80, 20:80] = 128
    bbox = BBox(x=30, y=30, width=40, height=40)
    patch = _crop_scaled_patch(img, bbox, scale=2.7)
    assert patch.shape == (80, 80, 3)


def test_silent_face_score_face(detector: SilentFaceDetector) -> None:
    # Synthetic face image
    img = np.full((120, 120, 3), 120, dtype=np.uint8)
    cv2.circle(img, (60, 60), 30, (200, 200, 200), -1)
    bbox = BBox(x=30, y=30, width=60, height=60)
    score = detector.score_face(img, bbox)
    assert 0.0 <= score <= 1.0


def test_check_frame_quality(detector: SilentFaceDetector) -> None:
    # Small face
    img = np.full((100, 100, 3), 120, dtype=np.uint8)
    face_small = Face(
        bbox=BBox(x=20, y=20, width=30, height=30),
        landmarks=((30.0, 30.0), (40.0, 30.0), (35.0, 35.0), (32.0, 42.0), (38.0, 42.0)),
        score=0.9,
    )
    qual = detector.check_frame_quality(img, face_small)
    assert not qual.passed and qual.reason == "face_too_small"

    # Touching margin
    face_margin = Face(
        bbox=BBox(x=2, y=2, width=70, height=70),
        landmarks=((20.0, 20.0), (50.0, 20.0), (35.0, 35.0), (25.0, 50.0), (45.0, 50.0)),
        score=0.9,
    )
    qual = detector.check_frame_quality(img, face_margin)
    assert not qual.passed and qual.reason == "face_touches_margin"

    # Good frame with texture (not blurry)
    rng = np.random.default_rng(42)
    textured = rng.integers(50, 180, size=(200, 200, 3), dtype=np.uint8)
    face_good = Face(
        bbox=BBox(x=20, y=20, width=120, height=120),
        landmarks=((50.0, 50.0), (100.0, 50.0), (75.0, 80.0), (60.0, 110.0), (90.0, 110.0)),
        score=0.9,
    )
    qual = detector.check_frame_quality(textured, face_good)
    assert qual.passed and qual.reason is None


def test_estimate_head_yaw(detector: SilentFaceDetector) -> None:
    # Center: eyes symmetric around nose (nose at x=100, right eye at x=70, left eye at x=130)
    # d_right = 30, d_left = 30 -> ratio = 0.50
    landmarks_center = (
        (70.0, 50.0),
        (130.0, 50.0),
        (100.0, 75.0),
        (80.0, 100.0),
        (120.0, 100.0),
    )
    ratio, pose = detector.estimate_head_yaw(landmarks_center)
    assert pose == "center" and 0.45 <= ratio <= 0.55

    # Turn left: nose moves rightward toward image left eye
    # e.g. nose at 118, left eye 130 -> d_left=12, d_right=48 -> ratio = 0.20
    landmarks_left = (
        (70.0, 50.0),
        (130.0, 50.0),
        (118.0, 75.0),
        (85.0, 100.0),
        (125.0, 100.0),
    )
    ratio, pose = detector.estimate_head_yaw(landmarks_left)
    assert pose == "left" and ratio < 0.38

    # Turn right: nose moves leftward toward image right eye
    # e.g. nose at 82 -> d_left=48, d_right=12 -> ratio = 0.80
    landmarks_right = (
        (70.0, 50.0),
        (130.0, 50.0),
        (82.0, 75.0),
        (75.0, 100.0),
        (115.0, 100.0),
    )
    ratio, pose = detector.estimate_head_yaw(landmarks_right)
    assert pose == "right" and ratio > 0.62


def test_check_blink_and_evaluate_frames(detector: SilentFaceDetector) -> None:
    rng = np.random.default_rng(42)
    img_front = rng.integers(60, 180, size=(200, 200, 3), dtype=np.uint8)
    face_front = Face(
        bbox=BBox(x=30, y=30, width=120, height=120),
        landmarks=((60.0, 60.0), (120.0, 60.0), (90.0, 90.0), (70.0, 120.0), (110.0, 120.0)),
        score=0.9,
    )

    # Blink frame: different eye texture
    img_blink = img_front.copy()
    img_blink[50:70, 50:70] = 40  # eye closed
    img_blink[50:70, 110:130] = 40
    face_blink = face_front

    blink_ok = detector.check_blink(img_front, face_front, img_blink, face_blink)
    assert blink_ok is True

    # Left frame
    img_left = rng.integers(60, 180, size=(200, 200, 3), dtype=np.uint8)
    face_left = Face(
        bbox=BBox(x=30, y=30, width=120, height=120),
        landmarks=((60.0, 60.0), (120.0, 60.0), (110.0, 90.0), (75.0, 120.0), (115.0, 120.0)),
        score=0.9,
    )

    # Right frame
    img_right = rng.integers(60, 180, size=(200, 200, 3), dtype=np.uint8)
    face_right = Face(
        bbox=BBox(x=30, y=30, width=120, height=120),
        landmarks=((60.0, 60.0), (120.0, 60.0), (70.0, 90.0), (65.0, 120.0), (105.0, 120.0)),
        score=0.9,
    )

    # Guided scan evaluation
    frames = [
        (img_front, face_front),
        (img_blink, face_blink),
        (img_left, face_left),
        (img_right, face_right),
    ]
    result = detector.evaluate_frames(frames, front_only=False)
    assert result.blink_detected is True
    assert result.turn_detected is True

    # Front-only fallback evaluation
    front_frames = [
        (img_front, face_front),
        (img_blink, face_blink),
        (img_front, face_front),
    ]
    result_front = detector.evaluate_frames(front_frames, front_only=True)
    assert result_front.blink_detected is True
    assert result_front.turn_detected is True
