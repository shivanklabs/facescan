"""AuraFace-v1 embedder (fal, Apache-2.0), primary engine: 512-d.

glintr100.onnx has no normalisation layer in its graph, so input pixels are RGB scaled to
(x - 127.5) / 127.5 (the ArcFace convention the model was trained with).
"""

import onnxruntime as ort

from facescan_ai.face_fingerprint.base import OnnxEmbedder

ENGINE = "auraface"
EXPECTED_DIM = 512


def create_auraface_embedder(session: ort.InferenceSession, version: str) -> OnnxEmbedder:
    """
    Purpose: AuraFace embedder from a verified session.
    Outputs: OnnxEmbedder (512-d). Side effects: none. Raises ValueError on a wrong model.
    """
    embedder = OnnxEmbedder(session, ENGINE, version, mean=127.5, std=127.5)
    if embedder.dim != EXPECTED_DIM:
        raise ValueError(f"AuraFace must be {EXPECTED_DIM}-d, got {embedder.dim}")
    return embedder
