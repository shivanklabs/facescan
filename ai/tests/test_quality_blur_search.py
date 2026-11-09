import cv2
import numpy as np
import pytest
from facescan_ai.alignment.align_5point import ARCFACE_TEMPLATE
from facescan_ai.index_store import EventIndex, IndexedFace
from facescan_ai.privacy.face_blur import blur_faces
from facescan_ai.quality.basic_quality import BasicFaceQuality, pose_components
from facescan_ai.search import rank_photos
from facescan_ai.types import BBox, Face


def face_from_points(points: np.ndarray, size: float = 112.0, score: float = 0.95) -> Face:
    lm = tuple((float(x), float(y)) for x, y in points)
    return Face(BBox(0, 0, size, size), lm, score)


def textured_crop(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(112, 112, 3), dtype=np.uint8)


def test_frontal_template_has_near_zero_pose() -> None:
    yaw, pitch, roll = pose_components(face_from_points(ARCFACE_TEMPLATE))
    assert yaw < 0.02 and pitch < 0.05 and roll < 1.0


def test_turned_head_scores_lower() -> None:
    q = BasicFaceQuality()
    frontal = face_from_points(ARCFACE_TEMPLATE)
    turned_points = ARCFACE_TEMPLATE.copy()
    turned_points[2, 0] += 18  # nose far to one side: strong yaw
    crop = textured_crop()
    good = q.assess(frontal, crop)
    bad = q.assess(face_from_points(turned_points), crop)
    assert 0.0 <= bad.value < good.value <= 1.0
    assert set(good.components) == {"size", "sharpness", "pose", "detector"}


def test_blurry_and_tiny_faces_score_lower() -> None:
    q = BasicFaceQuality()
    sharp = textured_crop()
    blurry = cv2.GaussianBlur(sharp, (15, 15), 6)
    frontal = face_from_points(ARCFACE_TEMPLATE)
    assert q.assess(frontal, blurry).value < q.assess(frontal, sharp).value
    tiny = face_from_points(ARCFACE_TEMPLATE, size=20.0)
    assert q.assess(tiny, sharp).value == 0.0


def test_blur_changes_face_region_only_and_copies() -> None:
    image = np.random.default_rng(3).integers(0, 256, size=(200, 300, 3), dtype=np.uint8)
    original = image.copy()
    face = Face(BBox(100, 60, 50, 50), ((0, 0),) * 5, 0.4)
    out = blur_faces(image, [face], margin=0.4)
    assert np.array_equal(image, original)  # input untouched
    region = (slice(40, 130), slice(80, 170))  # box plus 40% margin
    assert not np.array_equal(out[region], original[region])
    assert np.array_equal(out[:, :70], original[:, :70])
    assert np.array_equal(out[150:], original[150:])
    # Destroyed: far less detail than the random original.
    assert out[region].std() < original[region].std() / 2


def test_blur_validates_margin() -> None:
    with pytest.raises(ValueError):
        blur_faces(np.zeros((10, 10, 3), np.uint8), [], margin=-0.1)


def vec(values: list[float]) -> np.ndarray:
    a = np.array(values, dtype=np.float32)
    return a / np.linalg.norm(a)


def indexed(photo: str, aura: list[float], sface: list[float], quality: float) -> IndexedFace:
    return IndexedFace(
        photo_id=photo,
        bbox=BBox(0, 0, 50, 50),
        landmarks=((0, 0),) * 5,
        detector_score=0.9,
        quality=quality,
        embeddings={"auraface": vec(aura), "sface": vec(sface)},
    )


def test_rank_photos_best_face_per_photo() -> None:
    index = EventIndex(
        event="e1",
        engine_versions={},
        photos=["a.jpg", "b.jpg", "c.jpg"],
        faces=[
            indexed("a.jpg", [1, 0.1], [1, 0], 0.8),
            indexed("a.jpg", [0, 1], [0, 1], 0.9),
            indexed("b.jpg", [1, 1], [1, 1], 0.7),
            indexed("c.jpg", [-1, 0], [-1, 0], 0.6),
        ],
    )
    query = {"auraface": vec([1, 0]), "sface": vec([1, 0])}
    matches = rank_photos(index, query, "auraface", "sface", top=2)
    assert [m.photo_id for m in matches] == ["a.jpg", "b.jpg"]
    assert matches[0].face_quality == 0.8  # the matching face, not the other face in a.jpg
    assert matches[0].second_score == pytest.approx(1.0)
    assert rank_photos(EventIndex("e", {}), query, "auraface", "sface", 5) == []
    with pytest.raises(ValueError):
        rank_photos(index, query, "auraface", "sface", top=0)
