"""Rank an event's photos against one selfie (raw scores; release bands come in milestone 2).

Searches only the one event index it is given (invariant 2).
"""

from dataclasses import dataclass

import numpy as np

from facescan_ai.embedding.base import cosine_similarity_matrix
from facescan_ai.index_store import EventIndex
from facescan_ai.types import FloatArray


@dataclass(frozen=True, slots=True)
class PhotoMatch:
    """Best-matching face of one photo. Scores are cosine similarities in [-1, 1]."""

    photo_id: str
    primary_score: float
    second_score: float
    face_quality: float


def rank_photos(
    index: EventIndex,
    query: dict[str, FloatArray],
    primary_engine: str,
    second_engine: str,
    top: int,
) -> list[PhotoMatch]:
    """
    Purpose: score every face, keep the best face per photo (by the primary engine) and
        return the top photos.
    Inputs: event index, query vectors by engine (in memory only), engine names, top >= 1.
    Outputs: matches sorted by primary score, best first. Side effects: none.
    """
    if top < 1:
        raise ValueError("top must be >= 1")
    if not index.faces:
        return []
    primary = cosine_similarity_matrix(query[primary_engine], index.gallery(primary_engine))
    second = cosine_similarity_matrix(query[second_engine], index.gallery(second_engine))
    best: dict[str, int] = {}
    for i, face in enumerate(index.faces):
        current = best.get(face.photo_id)
        if current is None or primary[i] > primary[current]:
            best[face.photo_id] = i
    order = sorted(best.items(), key=lambda item: (-float(primary[item[1]]), item[0]))
    return [
        PhotoMatch(
            photo_id=photo_id,
            primary_score=round(float(primary[i]), 4),
            second_score=round(float(second[i]), 4),
            face_quality=index.faces[i].quality,
        )
        for photo_id, i in order[:top]
    ]


def all_finite(vectors: dict[str, FloatArray]) -> bool:
    """Purpose: guard against NaN/inf embeddings before searching. Side effects: none."""
    return all(bool(np.all(np.isfinite(v))) for v in vectors.values())
