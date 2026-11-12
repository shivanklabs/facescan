"""Cluster prototypes: quality-weighted mean of a cluster's best faces."""

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from facescan_ai.types import FloatArray


def build_prototype(
    members: Sequence[int],
    vectors: NDArray[np.float32],
    qualities: Sequence[float],
    cap: int,
) -> FloatArray:
    """
    Purpose: prototype of one cluster.
    Inputs: member row indices (strong faces only), embeddings, quality per row, cap >= 1 on
        how many of the best-quality members are used.
    Outputs: unit-length prototype. Side effects: none.
    Weights are the qualities (at least 0.05 each, so a single low-quality member still counts).
    """
    if cap < 1:
        raise ValueError("cap must be >= 1")
    if not members:
        raise ValueError("a cluster needs at least one member")
    best = sorted(members, key=lambda i: (-qualities[i], i))[:cap]
    weights = np.array([max(qualities[i], 0.05) for i in best], dtype=np.float64)
    mean = (vectors[best].astype(np.float64) * weights[:, None]).sum(axis=0) / weights.sum()
    norm = float(np.linalg.norm(mean))
    if norm <= 1e-12:
        raise ValueError("prototype is a zero vector")
    return (mean / norm).astype(np.float32)
