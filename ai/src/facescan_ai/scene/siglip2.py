"""SigLIP 2 scene understanding and query embedding (google/siglip2-base-patch16-224).

Runs image and text encoders in memory via ONNX Runtime with official tokenizer.json.
Embeddings are 768-dimensional float32 unit vectors.
"""

import threading
from typing import TYPE_CHECKING

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

from facescan_ai.model_registry import ModelRegistry
from facescan_ai.types import FloatArray, ImageBGR

if TYPE_CHECKING:
    from tokenizers import Tokenizer

SIGLIP2_IMAGE_ENCODER = "siglip2_image_encoder"
SIGLIP2_TEXT_ENCODER = "siglip2_text_encoder"
SIGLIP2_TOKENIZER = "siglip2_tokenizer"
MAX_TEXT_LENGTH = 64
EMBEDDING_DIM = 768


def preprocess_scene_image(image_bgr: ImageBGR) -> np.ndarray:
    """
    Purpose: SigLIP 2 image preprocessing matching preprocessor_config.json exactly:
      1. BGR -> RGB
      2. Pillow Bilinear resize to (224, 224)
      3. Rescale factor: 1.0 / 255.0
      4. Normalize: mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5] ((val / 255.0 - 0.5) / 0.5)
      5. Transpose to (1, 3, 224, 224) float32
    Inputs: BGR image. Outputs: (1, 3, 224, 224) float32 array.
    Side effects: none (in memory).
    """
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb).resize((224, 224), Image.Resampling.BILINEAR)
    tensor = np.asarray(pil_img, dtype=np.float32) / 255.0
    tensor = (tensor - 0.5) / 0.5
    tensor = np.transpose(tensor, (2, 0, 1))
    return np.ascontiguousarray(tensor[np.newaxis, ...], dtype=np.float32)


class Siglip2SceneEmbedder:
    """In-memory SigLIP 2 image and text embedding using ONNX Runtime."""

    def __init__(self, registry: ModelRegistry | None = None, threads: int = 0) -> None:
        """
        Purpose: prepare embedder state; models loaded lazily on first use.
        Inputs: optional ModelRegistry, intra-op threads.
        Outputs: none. Side effects: none.
        """
        self._registry = registry
        self._threads = threads
        self._img_session: ort.InferenceSession | None = None
        self._txt_session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None
        self._lock = threading.Lock()

    def _ensure_image_session(self) -> ort.InferenceSession:
        with self._lock:
            if self._img_session is None:
                reg = self._registry or ModelRegistry()
                _, self._img_session = reg.create_session(SIGLIP2_IMAGE_ENCODER, self._threads)
            return self._img_session

    def _ensure_text_session(self) -> tuple[ort.InferenceSession, "Tokenizer"]:
        with self._lock:
            if self._txt_session is None or self._tokenizer is None:
                from tokenizers import Tokenizer

                reg = self._registry or ModelRegistry()
                _, self._txt_session = reg.create_session(SIGLIP2_TEXT_ENCODER, self._threads)
                tok_path = reg.verified_path(SIGLIP2_TOKENIZER)
                tok = Tokenizer.from_file(str(tok_path))
                tok.enable_padding(length=MAX_TEXT_LENGTH, pad_id=0, pad_token="<pad>")  # noqa: S106
                tok.enable_truncation(max_length=MAX_TEXT_LENGTH)
                self._tokenizer = tok
            return self._txt_session, self._tokenizer

    def embed_image(self, image_bgr: ImageBGR) -> FloatArray:
        """
        Purpose: compute 768-dim L2-normalized scene embedding for one photo.
        Inputs: BGR image. Outputs: FloatArray of shape (768,).
        Side effects: none.
        """
        session = self._ensure_image_session()
        blob = preprocess_scene_image(image_bgr)
        outputs = session.run(["image_embeds"], {"pixel_values": blob})[0]
        vec = np.asarray(outputs[0], dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-12:
            vec = vec / norm
        return vec

    def tokenize(self, text: str) -> list[int]:
        """
        Purpose: tokenize lowercased text into 64 token IDs matching Siglip2Tokenizer.
        Inputs: text string. Outputs: list of 64 integer token IDs.
        Side effects: none.
        """
        _, tokenizer = self._ensure_text_session()
        encoded = tokenizer.encode(text.lower().strip())
        return list(encoded.ids)

    def embed_text(self, text_or_tokens: str | list[int]) -> FloatArray:
        """
        Purpose: compute 768-dim L2-normalized text embedding for search query.
        Inputs: text string or token IDs. Outputs: FloatArray of shape (768,).
        Side effects: none.
        """
        session, _ = self._ensure_text_session()
        ids = self.tokenize(text_or_tokens) if isinstance(text_or_tokens, str) else text_or_tokens
        ids_array = np.array([ids], dtype=np.int64)
        outputs = session.run(["text_embeds"], {"input_ids": ids_array})[0]
        vec = np.asarray(outputs[0], dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-12:
            vec = vec / norm
        return vec
