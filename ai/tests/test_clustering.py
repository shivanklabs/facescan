"""Synthetic-vector tests for clustering, constraints and prototypes (no images, no models)."""

import numpy as np
import pytest
from facescan_ai.clustering.constraints import (
    AttachConfig,
    apply_must_link,
    attach_weak_faces,
    has_conflict,
    split_cannot_link,
)
from facescan_ai.clustering.hdbscan_clusterer import ClusterConfig, cluster_event
from facescan_ai.clustering.prototypes import build_prototype

DIM = 64


def unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def people(n: int, seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [unit(rng.normal(size=DIM)) for _ in range(n)]


def sample(center: np.ndarray, rng: np.random.Generator, noise: float = 0.3) -> np.ndarray:
    return unit(center + rng.normal(size=DIM) * noise / np.sqrt(DIM))


def test_cannot_link_split_keeps_each_face_with_its_closest_members() -> None:
    a, b = people(2)
    rng = np.random.default_rng(1)
    vectors = np.stack(
        [sample(a, rng), sample(b, rng), sample(a, rng), sample(b, rng), sample(a, rng)]
    )
    photos = ["p1", "p1", "p2", "p3", "p4"]  # faces 0 (a) and 1 (b) share photo p1
    assert has_conflict([0, 1, 2, 3, 4], photos)
    groups = split_cannot_link([0, 1, 2, 3, 4], vectors, photos)
    assert sorted(groups) == [[0, 2, 4], [1, 3]]
    for g in groups:
        assert not has_conflict(g, photos)


def test_cannot_link_split_without_conflict_is_unchanged() -> None:
    vectors = np.stack(people(3))
    assert split_cannot_link([0, 1, 2], vectors, ["a", "b", "c"]) == [[0, 1, 2]]


def test_cannot_link_split_three_faces_in_one_photo() -> None:
    a, b, c = people(3, seed=4)
    rng = np.random.default_rng(2)
    vectors = np.stack([sample(a, rng), sample(b, rng), sample(c, rng), sample(c, rng)])
    groups = split_cannot_link([0, 1, 2, 3], vectors, ["x", "x", "x", "y"])
    assert sorted(groups) == [[0], [1], [2, 3]]


def test_weak_face_attaches_only_when_clearly_closest() -> None:
    a, b = people(2, seed=3)
    rng = np.random.default_rng(5)
    clear = sample(a, rng, 0.2)
    ambiguous = unit(a + b)  # equally close to both
    far = people(1, seed=99)[0]
    vectors = np.stack([sample(a, rng), sample(b, rng), clear, ambiguous, far])
    photos = ["p0", "p1", "p2", "p3", "p4"]
    prototypes = np.stack([a, b])
    attached = attach_weak_faces(
        [2, 3, 4], prototypes, [[0], [1]], vectors, photos, AttachConfig(0.4, 0.1)
    )
    assert attached == {2: 0}


def test_weak_face_never_joins_a_cluster_holding_its_photo() -> None:
    (a,) = people(1, seed=8)
    rng = np.random.default_rng(6)
    vectors = np.stack([sample(a, rng), sample(a, rng, 0.1)])
    attached = attach_weak_faces(
        [1], np.stack([a]), [[0]], vectors, ["same", "same"], AttachConfig(0.4, 0.1)
    )
    assert attached == {}


def test_must_link_merges_and_refuses_conflicts() -> None:
    clusters = [[0, 1], [2], [3]]
    photos = ["a", "b", "c", "a"]  # face 3 shares photo a with face 0
    merged, refused = apply_must_link(clusters, [(1, 2), (0, 3)], photos)
    assert sorted(merged) == [[0, 1, 2], [3]]
    assert refused == [(0, 3)]


def test_prototype_is_quality_weighted_and_capped() -> None:
    a, b = people(2, seed=11)
    vectors = np.stack([a, b, b])
    qualities = [0.9, 0.1, 0.1]
    proto = build_prototype([0, 1, 2], vectors, qualities, cap=10)
    assert float(proto @ a) > float(proto @ b)  # one good face outweighs two poor ones
    assert float(np.linalg.norm(proto)) == pytest.approx(1.0, abs=1e-6)
    capped = build_prototype([0, 1, 2], vectors, qualities, cap=1)
    assert np.allclose(capped, a, atol=1e-6)
    with pytest.raises(ValueError):
        build_prototype([], vectors, qualities, cap=1)


def synthetic_event(
    n_people: int, faces_each: int, seed: int = 0
) -> tuple[dict[str, np.ndarray], list[str], list[int]]:
    rng = np.random.default_rng(seed)
    centers = people(n_people, seed=seed + 100)
    rows, photos, who = [], [], []
    for p, c in enumerate(centers):
        for k in range(faces_each):
            rows.append(sample(c, rng, 0.35))
            photos.append(f"photo_{p}_{k}")
            who.append(p)
    v = np.stack(rows)
    return {"auraface": v, "sface": v.copy()}, photos, who


def test_cluster_event_finds_each_person_once() -> None:
    vectors, photos, who = synthetic_event(5, 6)
    clusters = cluster_event(
        vectors, "auraface", photos, [False] * len(photos), [0.8] * len(photos), ClusterConfig()
    )
    assert len(clusters) == 5  # regression: never one giant cluster for the whole event
    for c in clusters:
        assert len({who[i] for i in c.members}) == 1
        assert set(c.prototypes) == {"auraface", "sface"}


def test_weak_faces_never_form_or_anchor_clusters() -> None:
    vectors, photos, who = synthetic_event(2, 4, seed=3)
    weak = [who[i] == 1 for i in range(len(photos))]  # every face of person 1 is weak
    clusters = cluster_event(
        vectors, "auraface", photos, weak, [0.8] * len(photos), ClusterConfig()
    )
    for c in clusters:
        assert all(not weak[i] for i in c.members)
        assert all(who[i] == 0 for i in c.members)
    attached = [i for c in clusters for i in c.attached_weak]
    assert all(weak[i] for i in attached)


def test_cluster_event_handles_tiny_inputs() -> None:
    v = np.stack(people(1))
    one = cluster_event(
        {"auraface": v, "sface": v}, "auraface", ["p"], [False], [0.9], ClusterConfig()
    )
    assert [c.members for c in one] == [[0]]
    none = cluster_event(
        {"auraface": v, "sface": v}, "auraface", ["p"], [True], [0.1], ClusterConfig()
    )
    assert none == []
