"""Shared fixtures. Real-face tests read ai/tests/fixtures/local/ (git-ignored) and skip when the
folder, its manifest.json or the model weights are missing. Never commit face photos.

manifest.json (git-ignored, written by the owner of the photos):
    {"selfie": "selfie.jpg",
     "photos_with_me": ["a.jpg", ...],
     "photos_without_me": ["b.jpg", ...]}
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from facescan_ai.model_registry import ModelRegistry, ModelRegistryError

LOCAL_FIXTURES = Path(__file__).parent / "fixtures" / "local"
MANIFEST = LOCAL_FIXTURES / "manifest.json"


@dataclass(frozen=True)
class LocalFaces:
    """Consented local test photos described by manifest.json."""

    folder: Path
    selfie: Path
    with_me: list[Path]
    without_me: list[Path]


def _load_manifest() -> LocalFaces | None:
    if not MANIFEST.is_file():
        return None
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    selfie = LOCAL_FIXTURES / str(raw["selfie"])
    with_me = [LOCAL_FIXTURES / str(n) for n in raw.get("photos_with_me", [])]
    without_me = [LOCAL_FIXTURES / str(n) for n in raw.get("photos_without_me", [])]
    missing = [p.name for p in (selfie, *with_me, *without_me) if not p.is_file()]
    if missing:
        pytest.fail(f"manifest.json lists missing files: {missing}")
    return LocalFaces(LOCAL_FIXTURES, selfie, with_me, without_me)


@pytest.fixture(scope="session")
def local_faces() -> LocalFaces:
    """Purpose: the consented local photos, or skip when there are none."""
    faces = _load_manifest()
    if faces is None:
        pytest.skip("no ai/tests/fixtures/local/manifest.json (real-face tests skipped)")
    return faces


@pytest.fixture(scope="session")
def model_registry() -> ModelRegistry:
    """Purpose: registry with YuNet, SFace and AuraFace present and verified, or skip."""
    registry = ModelRegistry()
    try:
        for name in ("yunet", "sface", "auraface"):
            registry.read_verified_bytes(name)
    except ModelRegistryError as exc:
        pytest.skip(f"model weights unavailable ({exc}); run `uv run tasks models`")
    return registry
