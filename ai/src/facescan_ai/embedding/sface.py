"""SFace embedder (OpenCV Zoo, Apache-2.0), second opinion: 128-d.

The SFace graph starts with its own Sub/Mul normalisation, so it takes raw 0-255 RGB pixels
(as OpenCV's FaceRecognizerSF feeds it).
"""

import onnxruntime as ort

from facescan_ai.embedding.base import OnnxEmbedder

ENGINE = "sface"
EXPECTED_DIM = 128


def create_sface_embedder(session: ort.InferenceSession, version: str) -> OnnxEmbedder:
    """
    Purpose: SFace embedder from a verified session.
    Outputs: OnnxEmbedder (128-d). Side effects: none. Raises ValueError on a wrong model.
    """
    embedder = OnnxEmbedder(session, ENGINE, version, mean=0.0, std=1.0)
    if embedder.dim != EXPECTED_DIM:
        raise ValueError(f"SFace must be {EXPECTED_DIM}-d, got {embedder.dim}")
    return embedder
