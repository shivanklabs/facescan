"""End-to-end tests on consented local photos (skipped when they are not present)."""

import base64
import secrets
from pathlib import Path

import numpy as np
import pytest
from facescan_ai.cli import app
from facescan_ai.image_io import read_image
from facescan_ai.model_registry import ModelRegistry
from facescan_ai.pipeline import FacePipeline
from facescan_ai.privacy.face_blur import blur_faces
from typer.testing import CliRunner


@pytest.fixture(scope="module")
def pipeline(model_registry: ModelRegistry) -> FacePipeline:
    return FacePipeline(model_registry)


def best_score(pipeline: FacePipeline, photo: Path, query: np.ndarray) -> float:
    faces = pipeline.analyse(read_image(photo))
    if not faces:
        return -1.0
    return max(float(np.dot(f.embeddings["auraface"].vector, query)) for f in faces)


def test_selfie_produces_tagged_unit_embeddings(local_faces, pipeline: FacePipeline) -> None:
    analysed, faces_seen = pipeline.analyse_selfie(read_image(local_faces.selfie))
    assert faces_seen >= 1
    dims = {"auraface": 512, "sface": 128}
    for engine, emb in analysed.embeddings.items():
        assert emb.engine == engine
        assert emb.engine_version
        assert emb.vector.dtype == np.float32
        assert emb.vector.shape == (dims[engine],)
        assert np.all(np.isfinite(emb.vector))
        assert float(np.linalg.norm(emb.vector)) == pytest.approx(1.0, abs=1e-5)
    assert 0.0 <= analysed.quality.value <= 1.0


def test_photos_with_me_score_above_photos_without_me(local_faces, pipeline: FacePipeline) -> None:
    if not local_faces.with_me or not local_faces.without_me:
        pytest.skip("manifest needs photos_with_me and photos_without_me")
    selfie, _ = pipeline.analyse_selfie(read_image(local_faces.selfie))
    query = selfie.embeddings["auraface"].vector
    positives = [best_score(pipeline, p, query) for p in local_faces.with_me]
    negatives = [best_score(pipeline, p, query) for p in local_faces.without_me]
    assert min(positives) > max(negatives)


def test_cli_index_and_search_end_to_end(
    local_faces,
    model_registry: ModelRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACESCAN_BENCH_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("FACESCAN_BENCH_WORK_DIR", str(tmp_path / "work"))
    runner = CliRunner()
    indexed = runner.invoke(app, ["index", str(local_faces.folder), "--event", "e2e"])
    assert indexed.exit_code == 0, indexed.output
    assert "photos/second" in indexed.output
    assert (tmp_path / "work" / "e2e.fsidx").is_file()
    found = runner.invoke(app, ["search", str(local_faces.selfie), "--event", "e2e", "--top", "5"])
    assert found.exit_code == 0, found.output
    assert "auraface" in found.output
    cleaned = runner.invoke(app, ["clean", "--event", "e2e"])
    assert cleaned.exit_code == 0
    assert not (tmp_path / "work" / "e2e.fsidx").exists()


def test_blurred_faces_are_no_longer_recognisable(local_faces, pipeline: FacePipeline) -> None:
    image = read_image(local_faces.selfie)
    originals = pipeline.analyse(image)
    assert originals
    blurred = blur_faces(image, pipeline.blur_detector.detect(image))
    for leftover in pipeline.analyse(blurred):
        best = max(
            float(np.dot(leftover.embeddings["auraface"].vector, o.embeddings["auraface"].vector))
            for o in originals
        )
        # Different people score about 0.1-0.3 with AuraFace; a blurred face must not be closer.
        assert best < 0.2
