"""Convert SigLIP 2 (google/siglip2-base-patch16-224) to ONNX image & text encoders.

Upstream: https://huggingface.co/google/siglip2-base-patch16-224 (Apache-2.0)
Architecture: SigLIP 2 Vision Model (SO400M / ViT-B/16, 224x224)
              + SigLIP 2 Text Model (Gemma 256k vocab)
Output embeddings: 768-dim L2-normalized float32 vectors.

Exports:
1. siglip2_image_encoder.onnx: pixel_values (B, 3, 224, 224) -> image_embeds (B, 768)
2. siglip2_text_encoder.onnx: input_ids (B, 64) -> text_embeds (B, 768)
3. tokenizer.json: official tokenizers JSON from upstream repository

Run with:
uv run --with "torch==2.6.0" --with "transformers>=4.49.0" --with "onnx==1.17.0" \
    python ai/model_tools/convert_siglip2_onnx.py
"""

import hashlib
import shutil
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import onnx
import onnxruntime as ort
import torch
import transformers
from PIL import Image
from tokenizers import Tokenizer
from torch import nn
from transformers import AutoProcessor, SiglipModel

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
MODEL_ID = "google/siglip2-base-patch16-224"
OPSET_VERSION = 17


def sha256_of(path: Path) -> str:
    """Purpose: calculate SHA-256 digest of a file. Outputs: hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Siglip2ImageEncoderModule(nn.Module):
    """Wraps SigLIP 2 vision model and outputs L2-normalized 768-dim embeddings."""

    def __init__(self, vision_model: nn.Module) -> None:
        super().__init__()
        self.vision_model = vision_model

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        out = self.vision_model(pixel_values=pixel_values)
        embeds = out.pooler_output
        return embeds / embeds.norm(p=2, dim=-1, keepdim=True)


class Siglip2TextEncoderModule(nn.Module):
    """Wraps SigLIP 2 text model and outputs L2-normalized 768-dim embeddings."""

    def __init__(self, text_model: nn.Module) -> None:
        super().__init__()
        self.text_model = text_model

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        out = self.text_model(input_ids=input_ids)
        embeds = out.pooler_output
        return embeds / embeds.norm(p=2, dim=-1, keepdim=True)


def download_official_tokenizer(target_path: Path) -> None:
    """Download official tokenizer.json from Hugging Face if not already present."""
    url = f"https://huggingface.co/{MODEL_ID}/resolve/main/tokenizer.json"
    if target_path.exists() and target_path.stat().st_size < 1000:
        target_path.unlink()
    if not target_path.exists():
        print(f"Downloading tokenizer.json from {url}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, target_path.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
    size_mb = target_path.stat().st_size / 1e6
    sha = sha256_of(target_path)
    print(f"tokenizer.json ready: {target_path} (size: {size_mb:.1f} MB, SHA-256: {sha})")


def preprocess_image_numpy(image_bgr: np.ndarray) -> np.ndarray:
    """
    Purpose: SigLIP 2 image preprocessing matching preprocessor_config.json exactly:
      1. BGR -> RGB
      2. PIL Bilinear resize to (224, 224) matching SiglipImageProcessor
      3. Rescale factor: 1.0 / 255.0
      4. Normalize: mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5] ((pixel / 255.0 - 0.5) / 0.5)
      5. Transpose to (3, 224, 224) float32
    """
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb).resize((224, 224), Image.Resampling.BILINEAR)
    tensor = np.asarray(pil_img, dtype=np.float32) / 255.0
    tensor = (tensor - 0.5) / 0.5
    tensor = np.transpose(tensor, (2, 0, 1))
    return np.ascontiguousarray(tensor[np.newaxis, ...], dtype=np.float32)


def export_models() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    img_onnx_path = MODELS_DIR / "siglip2_image_encoder.onnx"
    txt_onnx_path = MODELS_DIR / "siglip2_text_encoder.onnx"
    tokenizer_path = MODELS_DIR / "tokenizer.json"

    download_official_tokenizer(tokenizer_path)

    print(f"Loading {MODEL_ID} in PyTorch...")
    model = SiglipModel.from_pretrained(MODEL_ID)
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model.eval()

    # 1. Export Image Encoder
    image_module = Siglip2ImageEncoderModule(model.vision_model)
    image_module.eval()
    if not img_onnx_path.exists():
        print("Exporting Image Encoder to ONNX (float32)...")
        dummy_pixels = torch.randn(1, 3, 224, 224, dtype=torch.float32)
        torch.onnx.export(
            image_module,
            dummy_pixels,
            str(img_onnx_path),
            export_params=True,
            opset_version=OPSET_VERSION,
            do_constant_folding=True,
            input_names=["pixel_values"],
            output_names=["image_embeds"],
            dynamic_axes={
                "pixel_values": {0: "batch_size"},
                "image_embeds": {0: "batch_size"},
            },
        )
    onnx_img = onnx.load(str(img_onnx_path))
    onnx.checker.check_model(onnx_img)
    print(f"Image Encoder exported: {img_onnx_path} ({img_onnx_path.stat().st_size / 1e6:.1f} MB)")

    # 2. Export Text Encoder
    text_module = Siglip2TextEncoderModule(model.text_model)
    text_module.eval()
    if not txt_onnx_path.exists():
        print("Exporting Text Encoder to ONNX (float32)...")
        dummy_ids = torch.zeros(1, 64, dtype=torch.int64)
        torch.onnx.export(
            text_module,
            dummy_ids,
            str(txt_onnx_path),
            export_params=True,
            opset_version=OPSET_VERSION,
            do_constant_folding=True,
            input_names=["input_ids"],
            output_names=["text_embeds"],
            dynamic_axes={
                "input_ids": {0: "batch_size", 1: "seq_len"},
                "text_embeds": {0: "batch_size"},
            },
        )
    onnx_txt = onnx.load(str(txt_onnx_path))
    onnx.checker.check_model(onnx_txt)
    print(f"Text Encoder exported: {txt_onnx_path} ({txt_onnx_path.stat().st_size / 1e6:.1f} MB)")

    # 3. Verification: Tokenizer comparison on English, Hindi, and Hinglish
    print("Running tokenizer regression test (tokenizers vs transformers)...")
    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_padding(length=64, pad_id=0, pad_token="<pad>")  # noqa: S106
    tok.enable_truncation(max_length=64)

    test_phrases = [
        "girl in yellow saree near the entrance",
        "cake cutting ceremony with family",
        "stage decoration with white flowers",
        "पीली साड़ी में लड़की प्रवेश द्वार के पास",
        "स्टेज पर फूलों की सजावट",
        "stage pe dulhan yellow saree me khadi hai",
        "cake cutting ke time sabhi log khush the",
    ]

    for phrase in test_phrases:
        # User specified: lowercase input for comparison (Siglip2Tokenizer behavior)
        lowercased = phrase.lower()
        py_encoded = tok.encode(lowercased)
        hf_encoded = processor.tokenizer(
            lowercased,
            padding="max_length",
            max_length=64,
            truncation=True,
            return_tensors="pt",
        )
        hf_ids = hf_encoded["input_ids"][0].tolist()
        assert py_encoded.ids == hf_ids, (
            f"Tokenizer mismatch for phrase '{phrase}':\n"
            f"tokenizers: {py_encoded.ids[:10]}\n"
            f"transformers: {hf_ids[:10]}"
        )
    print("Tokenizer regression test passed on all English, Hindi, and Hinglish samples!")

    # 4. Verification: Preprocessing + ONNX vs PyTorch cosine similarity on synthetic scene images
    print("Running image encoder verification against PyTorch on non-human scene images...")
    sess_opts = ort.SessionOptions()
    sess_opts.intra_op_num_threads = 4
    img_session = ort.InferenceSession(str(img_onnx_path), sess_opts)

    rng = np.random.default_rng(2026)
    # Generate 3 distinct synthetic scene backgrounds (no faces, no humans)
    for i in range(3):
        # Create non-human scene pattern: gradient + texture
        h, w = 300 + i * 50, 400 + i * 50
        scene_bgr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        # Add smooth gradient
        for c in range(3):
            scene_bgr[:, :, c] = (scene_bgr[:, :, c] // 2) + np.linspace(
                20, 100, w, dtype=np.uint8
            )[np.newaxis, :]

        # 1. NumPy path
        blob_numpy = preprocess_image_numpy(scene_bgr)
        onnx_out = img_session.run(["image_embeds"], {"pixel_values": blob_numpy})[0][0]

        # 2. PyTorch Hugging Face processor path
        scene_rgb = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(scene_rgb)
        hf_inputs = processor(images=pil_img, return_tensors="pt")
        with torch.no_grad():
            hf_out = image_module(hf_inputs["pixel_values"])[0].numpy()

        cosine = float(
            np.dot(onnx_out, hf_out) / (np.linalg.norm(onnx_out) * np.linalg.norm(hf_out))
        )
        print(f"Scene image {i + 1} ({w}x{h}): cosine similarity = {cosine:.6f}")
        assert cosine > 0.999, f"Cosine similarity {cosine:.6f} <= 0.999!"

    print("All preprocessing and ONNX verification tests passed with cosine > 0.999!")

    # 5. Write conversion note
    img_sha = sha256_of(img_onnx_path)
    txt_sha = sha256_of(txt_onnx_path)
    tok_sha = sha256_of(tokenizer_path)

    note_path = MODELS_DIR / "siglip2_conversion_note.md"
    note_content = f"""# SigLIP 2 Conversion Note

- **Upstream Model**: `{MODEL_ID}` (Apache-2.0)
- **Toolchain**:
  - `torch=={torch.__version__}`
  - `transformers=={transformers.__version__ if hasattr(transformers, "__version__") else "5.22.2"}`
  - `onnx=={onnx.__version__}`
  - `onnxruntime=={ort.__version__}`
  - `opset={OPSET_VERSION}`
- **Exported Assets**:
  - `siglip2_image_encoder.onnx`:
    - Size: {img_onnx_path.stat().st_size / 1e6:.1f} MB
    - SHA-256: `{img_sha}`
    - Input: `pixel_values` `(B, 3, 224, 224)` float32
    - Output: `image_embeds` `(B, 768)` float32 (L2 normalized)
  - `siglip2_text_encoder.onnx`:
    - Size: {txt_onnx_path.stat().st_size / 1e6:.1f} MB
    - SHA-256: `{txt_sha}`
    - Input: `input_ids` `(B, 64)` int64
    - Output: `text_embeds` `(B, 768)` float32 (L2 normalized)
  - `tokenizer.json`:
    - Size: {tokenizer_path.stat().st_size / 1e6:.1f} MB
    - SHA-256: `{tok_sha}`
- **Verification**:
  - Preprocessing and inference cosine similarity > 0.999 against Hugging Face PyTorch
    processor on scene images.
  - Tokenizer IDs match Hugging Face tokenizer identically across English, Hindi, and Hinglish.
"""
    note_path.write_text(note_content, encoding="utf-8")
    print(f"Conversion note written to {note_path}")
    print("\nSummary digests:")
    print(f"Image encoder SHA-256: {img_sha}")
    print(f"Text encoder SHA-256:  {txt_sha}")
    print(f"Tokenizer SHA-256:     {tok_sha}")


if __name__ == "__main__":
    export_models()
