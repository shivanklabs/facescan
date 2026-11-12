import math

import numpy as np
import pytest
from facescan_ai.face_alignment.align_face import (
    ALIGNED_SIZE,
    ARCFACE_TEMPLATE,
    align_face,
    alignment_matrix,
    similarity_transform,
)
from facescan_ai.types import BBox, Face


def apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    return points @ matrix[:, :2].T + matrix[:, 2]


def make_similarity(scale: float, degrees: float, tx: float, ty: float) -> np.ndarray:
    a = math.radians(degrees)
    r = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return np.hstack([scale * r, [[tx], [ty]]])


@pytest.mark.parametrize(
    ("scale", "degrees", "tx", "ty"), [(1, 0, 0, 0), (2.5, 30, 10, -4), (0.3, -75, 400, 220)]
)
def test_recovers_a_known_similarity(scale: float, degrees: float, tx: float, ty: float) -> None:
    src = np.array([[0, 0], [10, 0], [5, 7], [1, 12], [9, 12]], dtype=np.float64)
    truth = make_similarity(scale, degrees, tx, ty)
    estimate = similarity_transform(src, apply(truth, src))
    assert estimate == pytest.approx(truth, abs=1e-9)


def test_face_landmarks_map_onto_the_template() -> None:
    # A face that is the template scaled by 3, rotated 20 degrees and moved.
    forward = make_similarity(3.0, 20, 500, 300)
    landmarks = apply(forward, ARCFACE_TEMPLATE)
    face_points = tuple((float(x), float(y)) for x, y in landmarks)
    matrix = alignment_matrix(face_points)
    assert apply(matrix, landmarks) == pytest.approx(ARCFACE_TEMPLATE, abs=1e-6)


def test_degenerate_points_are_rejected() -> None:
    same = np.zeros((5, 2))
    with pytest.raises(ValueError):
        similarity_transform(same, ARCFACE_TEMPLATE)
    with pytest.raises(ValueError):
        similarity_transform(np.zeros((5, 3)), np.zeros((5, 3)))


def test_align_face_crops_112_and_keeps_landmark_pixels() -> None:
    image = np.zeros((400, 400, 3), dtype=np.uint8)
    lm = apply(make_similarity(2.0, 0, 100, 80), ARCFACE_TEMPLATE)
    nose_x, nose_y = (int(round(v)) for v in lm[2])
    image[nose_y - 3 : nose_y + 4, nose_x - 3 : nose_x + 4] = 255
    landmarks = (
        (float(lm[0, 0]), float(lm[0, 1])),
        (float(lm[1, 0]), float(lm[1, 1])),
        (float(lm[2, 0]), float(lm[2, 1])),
        (float(lm[3, 0]), float(lm[3, 1])),
        (float(lm[4, 0]), float(lm[4, 1])),
    )
    face = Face(BBox(100, 80, 224, 224), landmarks, 0.99)
    crop = align_face(image, face)
    assert crop.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)
    tx, ty = (int(round(v)) for v in ARCFACE_TEMPLATE[2])
    assert crop[ty, tx].max() == 255
