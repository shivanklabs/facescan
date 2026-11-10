"""Unit tests for liveness port/adapter and multi-angle matching."""

from uuid import uuid4

import numpy as np
from app.adapters.liveness.silent_face_liveness import SilentFaceLiveness
from app.adapters.vision.facescan_ai_face_analyzer import FacescanAiFaceAnalyzer
from app.ports.face_analyzer import BuiltCluster, SelfieQuery, StoredFace


def test_silent_face_liveness_capabilities() -> None:
    adapter = SilentFaceLiveness()
    assert adapter.capabilities.adapter_name == "silent_face"


def test_silent_face_liveness_rejects_corrupt_bytes() -> None:
    adapter = SilentFaceLiveness()
    verdict = adapter.verify_liveness([b"not_an_image", b"corrupt"])
    assert verdict.passed is False
    assert "frame_1_decode_error" in verdict.reasons


def test_multi_angle_matching_agreement_and_fallback() -> None:
    analyzer = FacescanAiFaceAnalyzer()

    # Create dummy stored faces belonging to one cluster
    photo1_id = uuid4()
    photo2_id = uuid4()
    face1_id = uuid4()
    face2_id = uuid4()

    # Orthogonal vectors for distinct identities
    vec_cluster1 = np.zeros(512, dtype=np.float32)
    vec_cluster1[0] = 1.0

    vec_cluster2 = np.zeros(512, dtype=np.float32)
    vec_cluster2[1] = 1.0

    # SFace vectors (128-dim)
    sface_c1 = np.zeros(128, dtype=np.float32)
    sface_c1[0] = 1.0

    sface_c2 = np.zeros(128, dtype=np.float32)
    sface_c2[1] = 1.0

    stored = [
        StoredFace(
            face_id=face1_id,
            photo_id=photo1_id,
            bbox=(10.0, 10.0, 80.0, 80.0),
            quality=0.9,
            detector_score=0.95,
            weak=False,
            embeddings={"auraface": vec_cluster1, "sface": sface_c1},
        ),
        StoredFace(
            face_id=face2_id,
            photo_id=photo2_id,
            bbox=(20.0, 20.0, 80.0, 80.0),
            quality=0.88,
            detector_score=0.92,
            weak=False,
            embeddings={"auraface": vec_cluster1, "sface": sface_c1},
        ),
    ]

    clusters = [
        BuiltCluster(
            members=[face1_id, face2_id],
            attached_weak=[],
            prototypes={"auraface": vec_cluster1, "sface": sface_c1},
        )
    ]

    calibration = {
        "auraface": {
            "release_threshold": 0.45,
            "borderline_threshold": 0.30,
            "n_same": 10,
            "n_diff": 10,
            "calibrated": True,
        },
        "sface": {
            "release_threshold": 0.40,
            "borderline_threshold": 0.25,
            "n_same": 10,
            "n_diff": 10,
            "calibrated": True,
        },
    }

    # Frame queries matching cluster 1
    front_query = SelfieQuery(
        vectors={"auraface": vec_cluster1.copy(), "sface": sface_c1.copy()},
        strong=True,
    )
    side_query_agree = SelfieQuery(
        vectors={"auraface": vec_cluster1.copy(), "sface": sface_c1.copy()},
        strong=True,
    )
    # Frame query matching different vector
    side_query_disagree = SelfieQuery(
        vectors={"auraface": vec_cluster2.copy(), "sface": sface_c2.copy()},
        strong=True,
    )

    # 1. Guided scan where side frames agree: release succeeds
    match_agree = analyzer.match_frames(
        stored,
        clusters,
        calibration,
        [front_query, front_query, side_query_agree, side_query_agree],
        front_only=False,
    )
    assert match_agree.frames_agree is True
    assert set(match_agree.release) == {photo1_id, photo2_id}

    # 2. Guided scan where a side frame disagrees: release is refused
    match_disagree = analyzer.match_frames(
        stored,
        clusters,
        calibration,
        [front_query, front_query, side_query_agree, side_query_disagree],
        front_only=False,
    )
    assert match_disagree.frames_agree is False
    assert len(match_disagree.release) == 0
    assert match_disagree.some_photos_missing is True

    # 3. Side frame cannot release alone against front frame verdict:
    # If front frame fails (e.g. orthogonal vector), side frame agreeing cannot release
    match_front_fail = analyzer.match_frames(
        stored,
        clusters,
        calibration,
        [side_query_disagree, side_query_disagree, side_query_agree, side_query_agree],
        front_only=False,
    )
    assert len(match_front_fail.release) == 0
    assert match_front_fail.frames_agree is False

    # 4. Front-only fallback mode applies stricter threshold
    # With a high threshold offset (e.g. 0.60 above 0.45 = 1.05 > 1.00 max cosine similarity),
    # release fails
    match_stricter = analyzer.match_frames(
        stored,
        clusters,
        calibration,
        [front_query, front_query, front_query],
        front_only=True,
        threshold_offset=0.60,
    )
    assert len(match_stricter.release) == 0
