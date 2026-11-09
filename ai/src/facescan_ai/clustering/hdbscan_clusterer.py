"""Groups the faces of one event into people with scikit-learn HDBSCAN.

Steps:
1. HDBSCAN on the L2-normalised AuraFace embeddings of strong (non-weak) faces. Euclidean
   distance on unit vectors orders pairs exactly like cosine similarity.
2. Purity guard: members far from their cluster's centroid leave the cluster.
3. Strong faces left over become one-face clusters (a person seen once can still be found).
4. Cannot-link: clusters holding two faces of one photo are split.
5. Must-link: confirmed pairs (from accepted claims, later) merge clusters if allowed.
6. Prototypes per engine from each cluster's best faces.
7. Weak faces attach to a cluster only when clearly closest to it; they never form clusters.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from sklearn.cluster import HDBSCAN

from facescan_ai.clustering.constraints import (
    AttachConfig,
    apply_must_link,
    attach_weak_faces,
    split_cannot_link,
)
from facescan_ai.clustering.prototypes import build_prototype
from facescan_ai.types import FloatArray


@dataclass(frozen=True, slots=True)
class ClusterConfig:
    """Clustering settings."""

    min_cluster_size: int = 2
    min_samples: int = 1
    cluster_selection_epsilon: float = 0.0
    # "eom" keeps each person in one cluster; "leaf" makes smaller, purer but more fragmented
    # clusters. allow_single_cluster stays off: it can return the whole event as one cluster.
    cluster_selection_method: str = "eom"
    min_member_similarity: float = 0.35
    singletons_as_clusters: bool = True
    prototype_cap: int = 10
    attach: AttachConfig = field(default_factory=AttachConfig)

    def __post_init__(self) -> None:
        """Purpose: reject invalid values. Side effects: none."""
        if self.min_cluster_size < 2 or self.min_samples < 1 or self.prototype_cap < 1:
            raise ValueError("min_cluster_size >= 2, min_samples >= 1, prototype_cap >= 1")
        if self.cluster_selection_method not in ("eom", "leaf"):
            raise ValueError("cluster_selection_method must be 'eom' or 'leaf'")


@dataclass(frozen=True, slots=True, eq=False)
class EventCluster:
    """One person-cluster: strong members, attached weak faces, prototype per engine."""

    cluster_id: int
    members: list[int]
    attached_weak: list[int]
    prototypes: dict[str, FloatArray]

    @property
    def all_faces(self) -> list[int]:
        """Members plus attached weak faces."""
        return sorted(self.members + self.attached_weak)

    def __repr__(self) -> str:
        """Purpose: debug text without prototype values."""
        return (
            f"EventCluster(id={self.cluster_id}, members={len(self.members)}, "
            f"weak={len(self.attached_weak)})"
        )


def run_hdbscan(vectors: NDArray[np.float32], config: ClusterConfig) -> list[int]:
    """
    Purpose: HDBSCAN labels (-1 = noise) for unit vectors.
    Inputs: N x D matrix. Outputs: label per row. Side effects: none.
    """
    n = vectors.shape[0]
    if n < config.min_cluster_size:
        return [-1] * n
    model = HDBSCAN(
        min_cluster_size=config.min_cluster_size,
        min_samples=config.min_samples,
        cluster_selection_epsilon=config.cluster_selection_epsilon,
        metric="euclidean",
        cluster_selection_method=config.cluster_selection_method,
        allow_single_cluster=False,
        copy=True,
    )
    return [int(v) for v in model.fit_predict(vectors.astype(np.float64))]


def _purify(
    members: list[int], vectors: NDArray[np.float32], min_similarity: float
) -> tuple[list[int], list[int]]:
    """Purpose: drop members whose cosine to the cluster centroid is too low."""
    if len(members) < 2:
        return members, []
    centroid = vectors[members].astype(np.float64).mean(axis=0)
    centroid /= max(float(np.linalg.norm(centroid)), 1e-12)
    keep = [m for m in members if float(np.dot(vectors[m], centroid)) >= min_similarity]
    ejected = [m for m in members if m not in keep]
    if not keep:
        return [], members
    return keep, ejected


def cluster_event(
    vectors_by_engine: dict[str, NDArray[np.float32]],
    primary_engine: str,
    photo_ids: Sequence[str],
    weak: Sequence[bool],
    qualities: Sequence[float],
    config: ClusterConfig,
    must_link: Sequence[tuple[int, int]] = (),
) -> list[EventCluster]:
    """
    Purpose: cluster one event's faces (see module docstring for the steps).
    Inputs: N x D matrix per engine (unit rows), the engine to cluster on, photo id, weak
        flag and quality per face, config, confirmed same-person pairs (never benchmark labels).
    Outputs: clusters with ids 0..K-1, largest first. Side effects: none.
    """
    primary = vectors_by_engine[primary_engine]
    strong = [i for i in range(len(photo_ids)) if not weak[i]]
    labels = run_hdbscan(primary[strong], config) if strong else []
    raw: dict[int, list[int]] = {}
    leftovers: list[int] = []
    for face, label in zip(strong, labels, strict=True):
        if label < 0:
            leftovers.append(face)
        else:
            raw.setdefault(label, []).append(face)
    groups: list[list[int]] = []
    for members in raw.values():
        kept, ejected = _purify(members, primary, config.min_member_similarity)
        if kept:
            groups.append(kept)
        leftovers.extend(ejected)
    if config.singletons_as_clusters:
        groups.extend([face] for face in sorted(leftovers))
    split: list[list[int]] = []
    for members in groups:
        split.extend(split_cannot_link(members, primary, photo_ids))
    merged, _refused = apply_must_link(split, must_link, photo_ids)
    merged = [g for g in merged if all(not weak[i] for i in g)]
    merged.sort(key=lambda g: (-len(g), g[0]))
    prototypes = {
        engine: np.stack(
            [build_prototype(g, vectors, qualities, config.prototype_cap) for g in merged]
        )
        if merged
        else np.zeros((0, vectors.shape[1]), dtype=np.float32)
        for engine, vectors in vectors_by_engine.items()
    }
    weak_faces = [i for i in range(len(photo_ids)) if weak[i]]
    attached = attach_weak_faces(
        weak_faces, prototypes[primary_engine], merged, primary, photo_ids, config.attach
    )
    return [
        EventCluster(
            cluster_id=k,
            members=members,
            attached_weak=sorted(f for f, c in attached.items() if c == k),
            prototypes={engine: protos[k] for engine, protos in prototypes.items()},
        )
        for k, members in enumerate(merged)
    ]
