"""Shared embedding code: the FaceEmbedder interface, ONNX runner and cosine similarity."""

from typing import Protocol

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from facescan_ai.face_alignment.align_face import ALIGNED_SIZE
from facescan_ai.types import Embedding, FloatArray, ImageBGR


class FaceEmbedder(Protocol):
    """Turns an aligned 112x112 BGR face crop into an L2-normalised embedding."""

    engine: str
    version: str
    dim: int

    def embed(self, aligned: ImageBGR) -> Embedding:
        """Purpose: embed one aligned crop. Side effects: none (in memory only)."""
        ...


def l2_normalise(vector: NDArray[np.float32]) -> FloatArray:
    """Purpose: scale a vector to unit length. Raises ValueError on a zero vector."""
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("cannot normalise a zero or non-finite vector")
    return (vector / norm).astype(np.float32)


def cosine_similarity(a: FloatArray, b: FloatArray) -> float:
    """
    Purpose: cosine similarity of two vectors (embeddings are unit length, so a dot product,
        but normalised here too so callers cannot get it wrong).
    Outputs: value in [-1, 1]. Side effects: none.
    """
    if a.shape != b.shape:
        raise ValueError("vectors must have the same shape")
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        raise ValueError("cannot compare zero vectors")
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


def cosine_similarity_matrix(query: FloatArray, gallery: NDArray[np.float32]) -> FloatArray:
    """Purpose: cosine of one query against N gallery rows. Outputs: N scores. No side effects."""
    if gallery.shape[0] == 0:
        return np.zeros((0,), dtype=np.float32)
    q = query / max(float(np.linalg.norm(query)), 1e-12)
    g = gallery / np.maximum(np.linalg.norm(gallery, axis=1, keepdims=True), 1e-12)
    return np.clip(g @ q, -1.0, 1.0).astype(np.float32)


class OnnxEmbedder:
    """Runs a 112x112 recognition model with model-specific pixel normalisation."""

    def __init__(
        self,
        session: ort.InferenceSession,
        engine: str,
        version: str,
        mean: float,
        std: float,
    ) -> None:
        """
        Purpose: wrap a verified recognition session.
        Inputs: session; engine name/version tag; input normalisation (pixel - mean) / std
            applied to RGB pixels. Side effects: none.
        """
        model_input = session.get_inputs()[0]
        if model_input.shape[1:] != [3, ALIGNED_SIZE, ALIGNED_SIZE]:
            raise ValueError(f"{engine}: unexpected input shape {model_input.shape}")
        self._session = session
        self._input_name = model_input.name
        self._mean = mean
        self._std = std
        self.engine = engine
        self.version = version
        out_dim = session.get_outputs()[0].shape[1]
        if not isinstance(out_dim, int) or out_dim <= 0:
            raise ValueError(f"{engine}: output size is not a fixed positive integer")
        self.dim = out_dim

    def embed(self, aligned: ImageBGR) -> Embedding:
        """
        Purpose: embed one aligned BGR crop.
        Outputs: unit-length float32 Embedding tagged with engine and version.
        Side effects: none.
        """
        if aligned.shape != (ALIGNED_SIZE, ALIGNED_SIZE, 3):
            raise ValueError("expected an aligned 112x112x3 crop")
        rgb = aligned[:, :, ::-1].astype(np.float32)
        blob = ((rgb - self._mean) / self._std).transpose(2, 0, 1)[None]
        output = self._session.run(None, {self._input_name: np.ascontiguousarray(blob)})[0]
        vector = np.asarray(output, dtype=np.float32).reshape(-1)
        if vector.shape[0] != self.dim:
            raise ValueError(f"{self.engine}: unexpected output size {vector.shape[0]}")
        return Embedding(
            vector=l2_normalise(vector), engine=self.engine, engine_version=self.version
        )
