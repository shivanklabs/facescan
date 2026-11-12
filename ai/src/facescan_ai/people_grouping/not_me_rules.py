"""Cannot-link and must-link constraints, and attachment of weak faces to clusters.

Vectors are rows of an N x D matrix of L2-normalised embeddings; faces are referred to by row
index. Clusters are lists of row indices.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


def _unit(v: NDArray[np.float64]) -> NDArray[np.float64]:
    """Purpose: L2-normalise (zero stays zero)."""
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-12 else v


def has_conflict(members: Sequence[int], photo_ids: Sequence[str]) -> bool:
    """Purpose: True if two members share a photo. Side effects: none."""
    photos = [photo_ids[i] for i in members]
    return len(photos) != len(set(photos))


def split_cannot_link(
    members: Sequence[int], vectors: NDArray[np.float32], photo_ids: Sequence[str]
) -> list[list[int]]:
    """
    Purpose: split a cluster so no two faces from the same photo stay together.
        The faces of the photo with the most members seed one group each; every other face
        joins the allowed group whose centroid it is most similar to (best pair first), and
        starts a new group when every group already holds a face from its photo.
    Inputs: member row indices, embedding matrix, photo id per row.
    Outputs: groups (each conflict-free), largest first. Side effects: none.
    """
    members = list(members)
    if not has_conflict(members, photo_ids):
        return [members]
    by_photo: dict[str, list[int]] = {}
    for i in members:
        by_photo.setdefault(photo_ids[i], []).append(i)
    seed_photo = max(by_photo, key=lambda p: (len(by_photo[p]), p))
    groups: list[list[int]] = [[i] for i in by_photo[seed_photo]]
    sums: list[NDArray[np.float64]] = [vectors[i].astype(np.float64) for i in by_photo[seed_photo]]
    remaining = [i for i in members if photo_ids[i] != seed_photo]
    while remaining:
        best: tuple[float, int, int] | None = None  # (similarity, face, group)
        for face in remaining:
            vec = vectors[face].astype(np.float64)
            for g, group in enumerate(groups):
                if any(photo_ids[m] == photo_ids[face] for m in group):
                    continue
                sim = float(np.dot(_unit(sums[g]), vec))
                if best is None or sim > best[0]:
                    best = (sim, face, g)
        if best is None:
            face = remaining[0]
            groups.append([face])
            sums.append(vectors[face].astype(np.float64))
            remaining.remove(face)
            continue
        _, face, g = best
        groups[g].append(face)
        sums[g] = sums[g] + vectors[face].astype(np.float64)
        remaining.remove(face)
    return sorted((sorted(g) for g in groups), key=lambda g: (-len(g), g[0]))


@dataclass(frozen=True, slots=True)
class AttachConfig:
    """When a weak face may join a cluster."""

    min_similarity: float = 0.40
    min_margin: float = 0.10


def attach_weak_faces(
    weak_faces: Sequence[int],
    prototypes: NDArray[np.float32],
    cluster_members: Sequence[Sequence[int]],
    vectors: NDArray[np.float32],
    photo_ids: Sequence[str],
    config: AttachConfig,
) -> dict[int, int]:
    """
    Purpose: attach weak faces only to a cluster they are clearly closer to than to any other.
        A weak face never joins a cluster that already holds a face from its photo.
    Inputs: weak row indices, K x D prototypes (unit), members per cluster, embeddings,
        photo ids, thresholds.
    Outputs: weak face -> cluster index for the faces that attach. Side effects: none.
    """
    attached: dict[int, int] = {}
    if prototypes.shape[0] == 0:
        return attached
    cluster_photos = [{photo_ids[m] for m in members} for members in cluster_members]
    for face in weak_faces:
        sims = prototypes @ vectors[face]
        order = np.argsort(-sims)
        best = int(order[0])
        second = float(sims[order[1]]) if len(order) > 1 else -1.0
        if photo_ids[face] in cluster_photos[best]:
            continue
        if float(sims[best]) >= config.min_similarity and (
            float(sims[best]) - second >= config.min_margin
        ):
            attached[face] = best
    return attached


def apply_must_link(
    clusters: list[list[int]],
    pairs: Sequence[tuple[int, int]],
    photo_ids: Sequence[str],
) -> tuple[list[list[int]], list[tuple[int, int]]]:
    """
    Purpose: merge clusters joined by confirmed same-person pairs (e.g. accepted guest claims).
        Never feed this from benchmark labels: that would leak ground truth into the result.
        A pair is refused when merging would put two faces of one photo together.
    Inputs: clusters (row-index lists), confirmed pairs of row indices, photo ids.
    Outputs: (new clusters, refused pairs). Side effects: none.
    """
    groups = [list(c) for c in clusters]
    refused: list[tuple[int, int]] = []

    def find(face: int) -> int | None:
        for g, members in enumerate(groups):
            if face in members:
                return g
        return None

    for a, b in pairs:
        ga, gb = find(a), find(b)
        if ga is None:
            groups.append([a])
            ga = len(groups) - 1
        if gb is None:
            groups.append([b])
            gb = len(groups) - 1
        if ga == gb:
            continue
        merged = groups[ga] + groups[gb]
        if has_conflict(merged, photo_ids):
            refused.append((a, b))
            continue
        groups[ga] = merged
        del groups[gb]
    return [sorted(g) for g in groups], refused
