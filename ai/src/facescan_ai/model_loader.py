"""Loads ONNX models listed in models/models.lock.json after verifying their SHA-256.

Rules (security invariant 9):
- Only models named in the lock file can be loaded; unknown names are refused.
- Entries whose URL or checksum is still TODO (liveness, OFIQ) are "unverified" and refused.
- The file's bytes are hashed and the session is created from those same bytes, so the file
  cannot be swapped between the check and the load.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import onnxruntime as ort

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
MODELS_DIR_ENV = "FACESCAN_AI_MODELS_DIR"
LOCK_FILE_NAME = "models.lock.json"
_SHA256_HEX_LENGTH = 64
_ORT_LOG_ERRORS_ONLY = 3


class ModelRegistryError(Exception):
    """Base error for model loading."""


class UnknownModelError(ModelRegistryError):
    """The model name is not in models.lock.json."""


class UnverifiedModelError(ModelRegistryError):
    """The lock entry has no pinned checksum or URL yet (TODO)."""


class ModelFileMissingError(ModelRegistryError):
    """The weights file is not downloaded. Run `uv run tasks models`."""


class ModelChecksumError(ModelRegistryError):
    """The weights file does not match the pinned SHA-256."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One entry of models.lock.json."""

    name: str
    version: str
    file: str
    sha256: str
    source_url: str
    licence: str

    @property
    def is_pinned(self) -> bool:
        """True when the checksum and URL are real values (not TODO)."""
        return (
            len(self.sha256) == _SHA256_HEX_LENGTH
            and all(c in "0123456789abcdef" for c in self.sha256)
            and self.source_url.startswith("https://")
            and "TODO" not in self.file
        )


def load_lock(lock_path: Path) -> dict[str, ModelSpec]:
    """
    Purpose: parse models.lock.json.
    Inputs: path. Outputs: name -> ModelSpec. Side effects: reads the file.
    """
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
    specs: dict[str, ModelSpec] = {}
    for entry in raw["models"]:
        spec = ModelSpec(
            name=str(entry["name"]),
            version=str(entry["version"]),
            file=str(entry["file"]),
            sha256=str(entry["sha256"]).lower(),
            source_url=str(entry["source_url"]),
            licence=str(entry["licence"]),
        )
        specs[spec.name] = spec
    return specs


class ModelRegistry:
    """Verified access to the pinned ONNX models."""

    def __init__(self, models_dir: Path | None = None) -> None:
        """
        Purpose: read the lock file.
        Inputs: models directory (default: FACESCAN_AI_MODELS_DIR or ai/models).
        Outputs: none. Side effects: reads models.lock.json.
        """
        configured = os.environ.get(MODELS_DIR_ENV)
        self.models_dir = models_dir or (Path(configured) if configured else DEFAULT_MODELS_DIR)
        self.specs = load_lock(self.models_dir / LOCK_FILE_NAME)

    def spec(self, name: str) -> ModelSpec:
        """Purpose: lock entry for a model. Raises UnknownModelError / UnverifiedModelError."""
        spec = self.specs.get(name)
        if spec is None:
            raise UnknownModelError(f"model '{name}' is not listed in {LOCK_FILE_NAME}")
        if not spec.is_pinned:
            raise UnverifiedModelError(f"model '{name}' has no pinned checksum yet")
        return spec

    def read_verified_bytes(self, name: str) -> tuple[ModelSpec, bytes]:
        """
        Purpose: read a model file and check its SHA-256.
        Outputs: (spec, file bytes). Side effects: reads the file.
        Raises ModelFileMissingError or ModelChecksumError.
        """
        spec = self.spec(name)
        path = self.models_dir / spec.file
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise ModelFileMissingError(
                f"{spec.file} is missing; run `uv run tasks models`"
            ) from exc
        if hashlib.sha256(data).hexdigest() != spec.sha256:
            raise ModelChecksumError(f"{spec.file} does not match the pinned SHA-256")
        return spec, data

    def verified_path(self, name: str) -> Path:
        """
        Purpose: return path to model asset after verifying its SHA-256.
        Inputs: model asset name.
        Outputs: Path. Side effects: reads and hashes file.
        """
        spec = self.spec(name)
        path = self.models_dir / spec.file
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise ModelFileMissingError(
                f"{spec.file} is missing; run `uv run tasks models`"
            ) from exc
        if hashlib.sha256(data).hexdigest() != spec.sha256:
            raise ModelChecksumError(f"{spec.file} does not match the pinned SHA-256")
        return path

    def create_session(self, name: str, threads: int = 0) -> tuple[ModelSpec, ort.InferenceSession]:
        """
        Purpose: build a CPU ONNX Runtime session from verified bytes.
        Inputs: model name; intra-op threads (0 = ONNX Runtime default).
        Outputs: (spec, session). Side effects: reads and hashes the file.
        """
        spec, data = self.read_verified_bytes(name)
        options = ort.SessionOptions()
        options.log_severity_level = _ORT_LOG_ERRORS_ONLY
        options.intra_op_num_threads = threads
        session = ort.InferenceSession(data, options, providers=["CPUExecutionProvider"])
        return spec, session
