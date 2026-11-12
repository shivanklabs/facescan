"""5-point similarity alignment to the standard 112x112 ArcFace template.

Both recognition models (AuraFace, SFace) were trained on crops aligned this way.
"""

import cv2
import numpy as np
from numpy.typing import NDArray

from facescan_ai.types import Face, ImageBGR, Landmarks

ALIGNED_SIZE = 112
# Right eye, left eye, nose tip, right mouth corner, left mouth corner (image coordinates).
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)

Matrix2x3 = NDArray[np.float64]


def similarity_transform(src: NDArray[np.float64], dst: NDArray[np.float64]) -> Matrix2x3:
    """
    Purpose: least-squares similarity (rotation, uniform scale, translation) mapping src to dst
        (Umeyama 1991, without reflection).
    Inputs: N x 2 source and destination points (N >= 2).
    Outputs: 2 x 3 affine matrix. Side effects: none.
    """
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2 or src.shape[0] < 2:
        raise ValueError("expected two N x 2 point arrays with N >= 2")
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_c, dst_c = src - src_mean, dst - dst_mean
    src_var = float((src_c**2).sum() / src.shape[0])
    if src_var <= 1e-12:
        raise ValueError("source points are degenerate")
    cov = dst_c.T @ src_c / src.shape[0]
    u, s, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1] = -1.0
    rotation = u @ np.diag(d) @ vt
    scale = float((s * d).sum() / src_var)
    translation = dst_mean - scale * rotation @ src_mean
    return np.hstack([scale * rotation, translation[:, None]])


def alignment_matrix(landmarks: Landmarks) -> Matrix2x3:
    """Purpose: transform from image coordinates to the 112x112 template. No side effects."""
    return similarity_transform(np.array(landmarks, dtype=np.float64), ARCFACE_TEMPLATE)


def align_face(image: ImageBGR, face: Face) -> ImageBGR:
    """
    Purpose: crop and align one face to 112x112 (BGR).
    Inputs: original image, detected face. Outputs: aligned crop (in memory only).
    Side effects: none.
    """
    matrix = alignment_matrix(face.landmarks)
    aligned = cv2.warpAffine(
        image, matrix, (ALIGNED_SIZE, ALIGNED_SIZE), flags=cv2.INTER_LINEAR, borderValue=0
    )
    return np.asarray(aligned, dtype=np.uint8)
