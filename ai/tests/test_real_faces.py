"""End-to-end tests on consented local photos (skipped when they are not present)."""

import base64
import secrets
from pathlib import Path

import numpy as np
import pytest
from facescan_ai.cli import app
from facescan_ai.face_pipeline import FacePipeline
from facescan_ai.image_loading import read_image
from facescan_ai.model_loader import ModelRegistry
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


def test_selfie_produces_tagged_unit_embeddings(single_face_selfie, pipeline: FacePipeline) -> None:
    analysed = pipeline.analyse_selfie(read_image(single_face_selfie.selfie))
    dims = {"auraface": 512, "sface": 128}
    for engine, emb in analysed.embeddings.items():
        assert emb.engine == engine
        assert emb.engine_version
        assert emb.vector.dtype == np.float32
        assert emb.vector.shape == (dims[engine],)
        assert np.all(np.isfinite(emb.vector))
        assert float(np.linalg.norm(emb.vector)) == pytest.approx(1.0, abs=1e-5)
    assert 0.0 <= analysed.quality.value <= 1.0


def test_photos_with_me_score_above_photos_without_me(
    single_face_selfie, pipeline: FacePipeline
) -> None:
    local_faces = single_face_selfie
    if not local_faces.with_me or not local_faces.without_me:
        pytest.skip("manifest needs photos_with_me and photos_without_me")
    selfie = pipeline.analyse_selfie(read_image(local_faces.selfie))
    query = selfie.embeddings["auraface"].vector
    positives = [best_score(pipeline, p, query) for p in local_faces.with_me]
    negatives = [best_score(pipeline, p, query) for p in local_faces.without_me]
    assert min(positives) > max(negatives)


def test_cli_index_match_and_search_end_to_end(
    single_face_selfie,
    model_registry: ModelRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACESCAN_BENCH_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("FACESCAN_BENCH_WORK_DIR", str(tmp_path / "work"))
    local_faces = single_face_selfie
    runner = CliRunner()
    indexed = runner.invoke(app, ["index", str(local_faces.folder), "--event", "e2e"])
    assert indexed.exit_code == 0, indexed.output
    assert "photos/second" in indexed.output
    assert (tmp_path / "work" / "e2e.fsidx").is_file()
    assert "clusters:" in indexed.output and "calibration:" in indexed.output
    found = runner.invoke(app, ["search", str(local_faces.selfie), "--event", "e2e", "--top", "5"])
    assert found.exit_code == 0, found.output
    assert "auraface" in found.output
    matched = runner.invoke(app, ["match", str(local_faces.selfie), "--event", "e2e"])
    assert matched.exit_code == 0, matched.output
    assert "RELEASE (" in matched.output and "borderline" in matched.output
    cleaned = runner.invoke(app, ["clean", "--event", "e2e"])
    assert cleaned.exit_code == 0
    assert not (tmp_path / "work" / "e2e.fsidx").exists()


def test_blurred_faces_are_no_longer_recognisable(local_faces, pipeline: FacePipeline) -> None:
    # Uses the whole fixture photo (any number of faces), not the selfie rule.
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
