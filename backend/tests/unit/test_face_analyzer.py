"""Quality-weighted fusion of selfie frames (connectors/face_ai/face_analyzer.py)."""

import numpy as np
from app.connectors.face_ai.face_analyzer import fuse_frames
from app.interfaces.face_analyzer import SelfieQuery


def _frame(vector: list[float], quality: float, strong: bool = True) -> SelfieQuery:
    v = np.array(vector, dtype=np.float32)
    return SelfieQuery(vectors={"e": v / np.linalg.norm(v)}, strong=strong, quality=quality)


def test_sharper_frames_pull_the_query_towards_them() -> None:
    sharp = _frame([1.0, 0.0], quality=0.9)
    blurred = _frame([0.0, 1.0], quality=0.1)
    fused = fuse_frames([sharp, blurred])["e"]
    assert np.isclose(np.linalg.norm(fused), 1.0)
    assert fused[0] > 0.9 > fused[1]  # mostly the sharp frame


def test_weak_frames_count_half_and_equal_frames_give_the_plain_mean() -> None:
    a = _frame([1.0, 0.0], quality=0.5)
    b = _frame([0.0, 1.0], quality=0.5)
    even = fuse_frames([a, b])["e"]
    assert np.isclose(even[0], even[1])
    weak_b = _frame([0.0, 1.0], quality=0.5, strong=False)
    leaning = fuse_frames([a, weak_b])["e"]
    assert leaning[0] > leaning[1]
