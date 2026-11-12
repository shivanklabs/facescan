"""Synthetic tests for calibration, margin, bands and the matcher."""

import numpy as np
import pytest
from facescan_ai.encrypted_face_index import EventIndex, IndexedFace
from facescan_ai.face_matching.calibration import (
    DEFAULT_THRESHOLDS,
    CalibrationConfig,
    EngineCalibration,
    calibrate,
    calibrate_engine,
    same_photo_pairs,
)
from facescan_ai.face_matching.confidence_levels import Band, Thresholds, cluster_band, face_band
from facescan_ai.face_matching.matcher import MatchConfig, match_event
from facescan_ai.face_matching.quality_gate import QualityGateConfig, is_weak
from facescan_ai.face_matching.winner_margin import margin_check
from facescan_ai.people_grouping.group_faces import EventCluster
from facescan_ai.types import BBox

T = Thresholds(
    primary_release=0.5, primary_borderline=0.4, second_release=0.45, second_borderline=0.36
)


def test_quality_gate() -> None:
    gate = QualityGateConfig(min_face_px=40, min_quality=0.45)
    assert not is_weak(40, 0.45, gate)
    assert is_weak(39.9, 0.9, gate)
    assert is_weak(200, 0.44, gate)
    with pytest.raises(ValueError):
        QualityGateConfig(min_quality=1.5)


def test_same_photo_pairs_skip_unusable_faces() -> None:
    pairs = same_photo_pairs(["a", "a", "a", "b", "b"], [True, True, False, True, True])
    assert pairs == [(0, 1), (3, 4)]


def test_calibrated_thresholds_hit_the_target_quantile() -> None:
    config = CalibrationConfig(release_fmr=0.01, borderline_fmr=0.1, pairs_per_fmr_unit=3)
    scores = np.linspace(0.0, 0.999, 1000).astype(np.float32)  # 1000 >= 3 / 0.01
    cal = calibrate_engine("auraface", scores, config)
    assert cal.release_calibrated and cal.borderline_calibrated
    assert cal.release_threshold == pytest.approx(0.99, abs=0.002)  # 99th percentile
    assert cal.borderline_threshold == pytest.approx(0.90, abs=0.002)
    assert cal.impostor_pairs == 1000


def test_calibration_falls_back_and_respects_floors() -> None:
    config = CalibrationConfig(release_fmr=0.001, borderline_fmr=0.01)
    few = np.full(50, 0.1, dtype=np.float32)
    cal = calibrate_engine("auraface", few, config)
    assert not cal.release_calibrated and not cal.borderline_calibrated
    assert cal.release_threshold == DEFAULT_THRESHOLDS["auraface"].release
    assert "DEFAULT" in cal.describe()
    # Plenty of very low impostor scores: calibrated, but never below the floors.
    low = np.full(5000, 0.01, dtype=np.float32)
    cal = calibrate_engine("auraface", low, config)
    assert cal.release_calibrated
    assert cal.release_threshold == DEFAULT_THRESHOLDS["auraface"].release_floor
    assert cal.borderline_threshold <= cal.release_threshold


def test_calibrate_uses_same_photo_pairs_only() -> None:
    rng = np.random.default_rng(0)
    v = rng.normal(size=(4, 8)).astype(np.float32)
    result = calibrate(
        {"auraface": v, "sface": v}, ["a", "a", "b", "c"], [True] * 4, CalibrationConfig()
    )
    assert result["auraface"].impostor_pairs == 1
    assert set(result) == {"auraface", "sface"}


def test_margin_check() -> None:
    r = margin_check(np.array([0.3, 0.7, 0.6], dtype=np.float32), 0.08)
    assert (r.best_index, r.ok) == (1, True)
    assert r.margin == pytest.approx(0.1)
    assert not margin_check(np.array([0.7, 0.65], dtype=np.float32), 0.08).ok
    single = margin_check(np.array([0.7], dtype=np.float32), 0.08)
    assert single.ok and single.second_score == -1.0
    assert margin_check(np.zeros((0,), np.float32), 0.08).best_index is None


@pytest.mark.parametrize(
    ("score", "margin_ok", "band"),
    [
        (0.6, True, Band.RELEASE),
        (0.6, False, Band.BORDERLINE),
        (0.45, True, Band.BORDERLINE),
        (0.3, True, Band.NONE),
    ],
)
def test_cluster_band(score: float, margin_ok: bool, band: Band) -> None:
    assert cluster_band(score, margin_ok, T) is band


@pytest.mark.parametrize(
    ("primary", "second", "cluster", "strong", "band"),
    [
        (0.6, 0.5, Band.RELEASE, True, Band.RELEASE),
        (0.6, 0.40, Band.RELEASE, True, Band.BORDERLINE),  # SFace disagrees
        (0.6, 0.5, Band.RELEASE, False, Band.BORDERLINE),  # weak selfie never releases
        (0.6, 0.5, Band.BORDERLINE, True, Band.BORDERLINE),  # cluster not released
        (0.45, 0.6, Band.RELEASE, True, Band.BORDERLINE),  # face below release threshold
        (0.3, 0.6, Band.RELEASE, True, Band.NONE),
        (0.9, 0.9, Band.NONE, True, Band.NONE),
    ],
)
def test_face_band(primary: float, second: float, cluster: Band, strong: bool, band: Band) -> None:
    assert face_band(primary, second, cluster, strong, T) is band


def test_thresholds_validate_order() -> None:
    with pytest.raises(ValueError):
        Thresholds(0.4, 0.5, 0.45, 0.36)


def unit(v: list[float]) -> np.ndarray:
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def face(photo: str, vec: np.ndarray, weak: bool = False) -> IndexedFace:
    return IndexedFace(
        photo,
        BBox(0, 0, 80, 80),
        ((0.0, 0.0),) * 5,
        0.9,
        0.8,
        {"auraface": vec, "sface": vec},
        weak,
    )


def make_index(second_cluster: np.ndarray) -> EventIndex:
    me = unit([1, 0, 0, 0])
    near_me = unit([1, 0.3, 0, 0])  # cosine about 0.96
    faces = [
        face("a.jpg", me),
        face("b.jpg", near_me),
        face("c.jpg", unit([0.6, 0.8, 0, 0]), weak=True),  # cosine 0.60: passes per-face check
        face("d.jpg", second_cluster),
        face("e.jpg", unit([0.45, 0.893, 0, 0]), weak=True),  # cosine 0.45: borderline only
    ]
    proto = unit([1, 0.15, 0, 0])
    clusters = [
        EventCluster(0, [0, 1], [2, 4], {"auraface": proto, "sface": proto}),
        EventCluster(1, [3], [], {"auraface": second_cluster, "sface": second_cluster}),
    ]
    cal = {e: EngineCalibration(e, 0.5, 0.4, 0, False, False, None) for e in ("auraface", "sface")}
    photos = ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]
    return EventIndex("e", {}, photos, faces, clusters=clusters, calibration=cal)


def test_match_releases_winning_cluster_faces_that_pass_the_per_face_check() -> None:
    index = make_index(unit([0, 0, 1, 0]))
    query = {"auraface": unit([1, 0, 0, 0]), "sface": unit([1, 0, 0, 0])}
    result = match_event(index, query, True, MatchConfig(0.08))
    assert result.cluster_id == 0 and result.margin_ok
    # Weak attached faces are released only through the strong cluster match plus the
    # per-face check: c passes it (0.60), e does not (0.45) and stays borderline.
    assert result.release_photos == ["a.jpg", "b.jpg", "c.jpg"]
    assert result.borderline_photos == ["e.jpg"]
    assert "d.jpg" not in result.release_photos + result.borderline_photos


def test_match_close_second_cluster_blocks_release() -> None:
    index = make_index(unit([1, 0.2, 0, 0]))  # a second cluster almost as close
    query = {"auraface": unit([1, 0, 0, 0]), "sface": unit([1, 0, 0, 0])}
    result = match_event(index, query, True, MatchConfig(0.08))
    assert not result.margin_ok
    assert result.release_photos == []
    assert result.borderline_photos


def test_match_weak_selfie_never_releases_and_empty_index() -> None:
    index = make_index(unit([0, 0, 1, 0]))
    query = {"auraface": unit([1, 0, 0, 0]), "sface": unit([1, 0, 0, 0])}
    assert match_event(index, query, False, MatchConfig()).release_photos == []
    empty = EventIndex("e", {})
    assert match_event(empty, query, True, MatchConfig()).cluster_id is None


def test_match_debug_scores_present_on_evaluated_faces() -> None:
    index = make_index(unit([0, 0, 1, 0]))
    query = {"auraface": unit([1, 0, 0, 0]), "sface": unit([1, 0, 0, 0])}
    result = match_event(index, query, True, MatchConfig(0.08))
    assert result.faces
    for face in result.faces:
        assert isinstance(face.primary_score, float)
        assert isinstance(face.second_score, float)
        assert -1.0 <= face.primary_score <= 1.0
        assert -1.0 <= face.second_score <= 1.0
        assert face.band in (Band.RELEASE, Band.BORDERLINE, Band.NONE)
