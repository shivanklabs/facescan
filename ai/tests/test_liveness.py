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

    # Front-only burst: the blink can be in any middle frame (eyes open in the others).
    burst = [
        (img_front, face_front),
        (img_front, face_front),
        (img_front, face_front),
        (img_blink, face_blink),
        (img_front, face_front),
        (img_front, face_front),
    ]
    assert detector.evaluate_frames(burst, front_only=True).blink_detected is True

    # No blink anywhere in the burst is still refused.
    no_blink = [(img_front, face_front)] * 6
    result_none = detector.evaluate_frames(no_blink, front_only=True)
    assert result_none.blink_detected is False
    assert "blink_not_detected" in result_none.reasons

    # require_blink=False (dev-only "look" scan) skips only the blink challenge.
    result_look = detector.evaluate_frames(no_blink, front_only=True, require_blink=False)
    assert "blink_not_detected" not in result_look.reasons
    assert result_look.is_live == result_none.is_live


def _upstream_crop_reference(
    image: np.ndarray, bbox: tuple[int, int, int, int], scale: float
) -> np.ndarray:
    """
    Purpose: reference implementation matching upstream generate_patches.py CropImage._get_new_box.
    Inputs: image (H, W, 3), bbox (x, y, w, h), scale factor.
    Outputs: cropped, resized (80, 80, 3) BGR array.
    Side effects: none.
    """
    src_h, src_w = image.shape[:2]
    scale = min((src_h - 1) / max(1, bbox[3]), min((src_w - 1) / max(1, bbox[2]), scale))
    new_width = bbox[2] * scale
    new_height = bbox[3] * scale
    center_x = bbox[2] / 2.0 + bbox[0]
    center_y = bbox[3] / 2.0 + bbox[1]

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
    return np.asarray(cv2.resize(crop, (80, 80)), dtype=np.uint8)


def test_silent_face_preprocessing_regression_synthetic(detector: SilentFaceDetector) -> None:
    """
    Purpose: regression test that Silent-Face preprocessing preserves [0, 255] range (no / 255),
        BGR channel order, 80x80 output, and feeds unnormalized floats to the ONNX session.
    Inputs: detector fixture.
    Outputs: None.
    Side effects: none.
    """
    # Create purely synthetic image with known distinct BGR channels
    # Blue=80, Green=140, Red=220
    b_val, g_val, r_val = 80, 140, 220
    img = np.zeros((150, 150, 3), dtype=np.uint8)
    img[:, :, 0] = b_val
    img[:, :, 1] = g_val
    img[:, :, 2] = r_val
    bbox = BBox(x=25, y=25, width=80, height=80)

    # 1. Output size is strictly 80x80x3
    patch_27 = _crop_scaled_patch(img, bbox, scale=2.7)
    patch_40 = _crop_scaled_patch(img, bbox, scale=4.0)
    assert patch_27.shape == (80, 80, 3)
    assert patch_40.shape == (80, 80, 3)

    # 2. BGR channel order is preserved in the crop
    assert np.all(patch_27[:, :, 0] == b_val)
    assert np.all(patch_27[:, :, 1] == g_val)
    assert np.all(patch_27[:, :, 2] == r_val)

    # 3. Verify blob fed into session has shape (1, 3, 80, 80), BGR order, and [0, 255] range
    recorded_inputs: list[dict[str, np.ndarray]] = []

    class MockSession:
        def run(
            self, output_names: list[str], feed_dict: dict[str, np.ndarray]
        ) -> list[np.ndarray]:
            recorded_inputs.append(feed_dict)
            return [np.array([[0.1, 0.9, 0.0]], dtype=np.float32)]

    score = detector._predict_model(img, bbox, scale=2.7, session=MockSession())  # type: ignore[arg-type]
    assert score == pytest.approx(0.9)
    assert len(recorded_inputs) == 1
    blob = recorded_inputs[0]["input"]

    # Blob shape: (1, 3, 80, 80)
    assert blob.shape == (1, 3, 80, 80)
    assert blob.dtype == np.float32
    # Channel 0 is Blue, Channel 1 is Green, Channel 2 is Red
    assert np.all(blob[0, 0, :, :] == float(b_val))
    assert np.all(blob[0, 1, :, :] == float(g_val))
    assert np.all(blob[0, 2, :, :] == float(r_val))
    # Crucial regression check: values must NOT be scaled down by 255.0
    assert float(np.max(blob)) > 1.0
    assert float(np.max(blob)) == float(r_val)


def test_silent_face_crop_clamping_matches_upstream_for_edges_and_scales() -> None:
    """
    Purpose: regression test that _crop_scaled_patch boundary clamping inside image matches
        upstream generate_patches.py for boxes near top, bottom, left, right edges, and corners,
        for both scale 2.7 and scale 4.0 using a synthetic texture image.
    Inputs: None.
    Outputs: None.
    Side effects: none.
    """
    rng = np.random.default_rng(2026)
    # Synthetic textured background where every pixel coordinate has distinct values
    h, w = 320, 320
    img = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)

    edge_boxes = [
        # Near left edge
        BBox(x=2, y=100, width=50, height=50),
        BBox(x=0, y=80, width=60, height=60),
        # Near right edge
        BBox(x=w - 52, y=100, width=50, height=50),
        BBox(x=w - 40, y=80, width=40, height=40),
        # Near top edge
        BBox(x=100, y=2, width=50, height=50),
        BBox(x=80, y=0, width=60, height=60),
        # Near bottom edge
        BBox(x=100, y=h - 52, width=50, height=50),
        BBox(x=80, y=h - 40, width=40, height=40),
        # Corners
        BBox(x=1, y=1, width=45, height=45),
        BBox(x=w - 46, y=h - 46, width=45, height=45),
        BBox(x=1, y=h - 46, width=45, height=45),
        BBox(x=w - 46, y=1, width=45, height=45),
    ]

    for scale in (2.7, 4.0):
        for box in edge_boxes:
            patch = _crop_scaled_patch(img, box, scale=scale)
            expected = _upstream_crop_reference(
                img, (box.x, box.y, box.width, box.height), scale=scale
            )
            assert patch.shape == (80, 80, 3)
            assert np.array_equal(patch, expected), (
                f"Mismatch for box {box} at scale {scale} compared to upstream reference"
            )
