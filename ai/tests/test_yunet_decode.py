import math

import numpy as np
import pytest
from facescan_ai.detection.yunet import STRIDES, decode_yunet

SIZE = 640


def empty_outputs() -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for s in STRIDES:
        n = (SIZE // s) ** 2
        out[f"cls_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"obj_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"bbox_{s}"] = np.zeros((1, n, 4), np.float32)
        out[f"kps_{s}"] = np.zeros((1, n, 10), np.float32)
    return out


def test_decodes_one_cell() -> None:
    out = empty_outputs()
    stride, row, col = 16, 3, 5
    idx = row * (SIZE // stride) + col
    out["cls_16"][0, idx, 0] = 0.81
    out["obj_16"][0, idx, 0] = 1.0
    out["bbox_16"][0, idx] = [0.5, 0.25, math.log(2.0), math.log(3.0)]
    out["kps_16"][0, idx] = [0.0, 0.0, 1.0, 0.0, 0.5, 0.5, 0.0, 1.0, 1.0, 1.0]
    b, k, s = decode_yunet(out, SIZE, 0.5)
    assert s.tolist() == pytest.approx([0.9])
    cx, cy, w, h = (col + 0.5) * stride, (row + 0.25) * stride, 2 * stride, 3 * stride
    assert b[0].tolist() == pytest.approx([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
    assert k[0, 0].tolist() == pytest.approx([col * stride, row * stride])
    assert k[0, 4].tolist() == pytest.approx([(col + 1) * stride, (row + 1) * stride])


def test_threshold_and_clipping() -> None:
    out = empty_outputs()
    out["cls_8"][0, 0, 0] = 2.0  # clipped to 1
    out["obj_8"][0, 0, 0] = 0.25  # score sqrt(0.25) = 0.5
    _, _, s = decode_yunet(out, SIZE, 0.6)
    assert s.size == 0
    _, _, s = decode_yunet(out, SIZE, 0.5)
    assert s.tolist() == pytest.approx([0.5])


def test_no_detections_have_right_shapes() -> None:
    b, k, s = decode_yunet(empty_outputs(), SIZE, 0.5)
    assert b.shape == (0, 4) and k.shape == (0, 5, 2) and s.shape == (0,)
