"""Selfie rule: exactly one face. Unit tests with synthetic faces; real-image tests use the
local fixture photo (crops made in memory) and skip without it."""

import numpy as np
import pytest
from facescan_ai.face_matching.quality_gate import QualityGateConfig
from facescan_ai.face_pipeline import FacePipeline, SelfieFaceCountError, require_single_face
from facescan_ai.image_loading import read_image
from facescan_ai.model_loader import ModelRegistry
from facescan_ai.types import BBox, Face


def fake_face(x: float) -> Face:
    return Face(BBox(x, 0, 50, 50), ((0.0, 0.0),) * 5, 0.9)


def test_zero_faces_refused() -> None:
    with pytest.raises(SelfieFaceCountError) as info:
        require_single_face([])
    assert info.value.count == 0
    assert "found 0" in str(info.value)


def test_one_face_accepted() -> None:
    face = fake_face(1)
    assert require_single_face([face]) is face


@pytest.mark.parametrize("count", [2, 5])
def test_several_faces_refused(count: int) -> None:
    with pytest.raises(SelfieFaceCountError) as info:
        require_single_face([fake_face(i * 60) for i in range(count)])
    assert info.value.count == count
    assert f"found {count}" in str(info.value)


@pytest.fixture(scope="module")
def pipeline(model_registry: ModelRegistry) -> FacePipeline:
    return FacePipeline(model_registry)


def test_real_images_zero_one_and_many(local_faces, pipeline: FacePipeline) -> None:
    gate = QualityGateConfig()
    photo = read_image(local_faces.selfie)
    faces = pipeline.detector.detect(photo)
    # 0 faces: a blank frame.
    with pytest.raises(SelfieFaceCountError) as none:
        pipeline.analyse_selfie_frames([np.full((480, 640, 3), 128, np.uint8)], gate)
    assert none.value.count == 0
    # 2+ faces: the whole photo, when it shows several people.
    if len(faces) >= 2:
        with pytest.raises(SelfieFaceCountError) as many:
            pipeline.analyse_selfie_frames([photo], gate)
        assert many.value.count == len(faces)
    # 1 face: an in-memory crop around the largest face, with no other face inside.
    largest = max(faces, key=lambda f: f.bbox.width * f.bbox.height)
    b = largest.bbox
    pad = 0.3 * b.width
    x1, y1 = max(0, int(b.x - pad)), max(0, int(b.y - pad))
    x2 = min(photo.shape[1], int(b.x2 + pad))
    y2 = min(photo.shape[0], int(b.y2 + pad))
    others_inside = [
        f
        for f in faces
        if f is not largest
        and f.bbox.x2 > x1
        and f.bbox.x < x2
        and f.bbox.y2 > y1
        and f.bbox.y < y2
    ]
    if others_inside:
        pytest.skip("faces too close together to crop a single-face frame")
    query = pipeline.analyse_selfie_frames([np.ascontiguousarray(photo[y1:y2, x1:x2])], gate)
    assert query.frames == 1
    assert set(query.vectors) == {"auraface", "sface"}
    assert "vectors" not in repr(query)
