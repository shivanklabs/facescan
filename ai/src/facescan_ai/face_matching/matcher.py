"""Selfie-to-event matching: cluster prototypes -> margin -> per-face check -> bands.

1. Score the selfie against every cluster prototype (primary engine, AuraFace).
2. Take the best and second-best cluster; the margin check needs a clear winner.
3. Cluster band: release needs the prototype score above the release threshold and a clear
   margin; borderline needs the borderline threshold.
4. Per-face check: every face of the winning cluster (members and attached weak faces) is
   scored against the selfie with both engines. Release needs the primary engine above its
   release threshold AND SFace agreeing above its own; a weak selfie never releases.
5. Photos with a released face are released; photos with only borderline faces are listed as
   borderline (debug output, never shown to guests).
Only the given event index is searched (invariant 2).
"""

from dataclasses import dataclass

import numpy as np

from facescan_ai.encrypted_face_index import EventIndex
from facescan_ai.face_fingerprint.base import cosine_similarity_matrix
from facescan_ai.face_matching.calibration import DEFAULT_THRESHOLDS
from facescan_ai.face_matching.confidence_levels import Band, Thresholds, cluster_band, face_band
from facescan_ai.face_matching.winner_margin import margin_check
from facescan_ai.types import FloatArray

PRIMARY_ENGINE = "auraface"
SECOND_ENGINE = "sface"


@dataclass(frozen=True, slots=True)
class MatchConfig:
    """Matching settings."""

    min_margin: float = 0.08
    threshold_offset: float = 0.0

    def __post_init__(self) -> None:
        """Purpose: reject invalid values. Side effects: none."""
        if not 0.0 <= self.min_margin < 1.0:
            raise ValueError("min_margin must be in [0, 1)")
        if not 0.0 <= self.threshold_offset < 1.0:
            raise ValueError("threshold_offset must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class FaceDecision:
    """Per-face outcome inside the winning cluster (scores only, no vectors)."""

    face_index: int
    photo_id: str
    primary_score: float
    second_score: float
    weak: bool
    band: Band


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome of matching one selfie against one event."""

    release_photos: list[str]
    borderline_photos: list[str]
    cluster_id: int | None
    cluster_band: Band
    best_score: float
    second_score: float
    margin: float
    margin_ok: bool
    selfie_strong: bool
    thresholds: Thresholds
    faces: list[FaceDecision]


def thresholds_for(index: EventIndex, offset: float = 0.0) -> Thresholds:
    """
    Purpose: release/borderline thresholds from the event's calibration (defaults if absent),
        with an optional offset (used by stricter front-only matching).
    Side effects: none.
    """

    def pick(engine: str) -> tuple[float, float]:
        cal = index.calibration.get(engine)
        if cal is not None:
            return cal.release_threshold + offset, cal.borderline_threshold + offset
        d = DEFAULT_THRESHOLDS[engine]
        return d.release + offset, d.borderline + offset

    p_rel, p_bord = pick(PRIMARY_ENGINE)
    s_rel, s_bord = pick(SECOND_ENGINE)
    return Thresholds(p_rel, p_bord, s_rel, s_bord)


def match_event(
    index: EventIndex,
    query: dict[str, FloatArray],
    selfie_strong: bool,
    config: MatchConfig,
) -> MatchResult:
    """
    Purpose: match one selfie query (unit vectors per engine) against one event.
    Inputs: event index with clusters, query vectors (in memory only), whether the selfie
        passed the quality gate, config.
    Outputs: MatchResult with released and borderline photo ids. Side effects: none.
    """
    thresholds = thresholds_for(index, config.threshold_offset)
    empty = MatchResult(
        [], [], None, Band.NONE, -1.0, -1.0, 0.0, False, selfie_strong, thresholds, []
    )
    if not index.clusters:
        return empty
    prototypes = np.stack([c.prototypes[PRIMARY_ENGINE] for c in index.clusters])
    margin = margin_check(
        cosine_similarity_matrix(query[PRIMARY_ENGINE], prototypes), config.min_margin
    )
    if margin.best_index is None:
        return empty
    winner = index.clusters[margin.best_index]
    band = cluster_band(margin.best_score, margin.ok, thresholds)
    face_rows = winner.all_faces
    primary = cosine_similarity_matrix(
        query[PRIMARY_ENGINE],
        np.stack([index.faces[i].embeddings[PRIMARY_ENGINE] for i in face_rows]),
    )
    second = cosine_similarity_matrix(
        query[SECOND_ENGINE],
        np.stack([index.faces[i].embeddings[SECOND_ENGINE] for i in face_rows]),
    )
    decisions = []
    for row, p_score, s_score in zip(face_rows, primary, second, strict=True):
        face = index.faces[row]
        decisions.append(
            FaceDecision(
                face_index=row,
                photo_id=face.photo_id,
                primary_score=round(float(p_score), 4),
                second_score=round(float(s_score), 4),
                weak=face.weak,
                band=face_band(float(p_score), float(s_score), band, selfie_strong, thresholds),
            )
        )
    released = sorted({d.photo_id for d in decisions if d.band is Band.RELEASE})
    borderline = sorted(
        {d.photo_id for d in decisions if d.band is Band.BORDERLINE} - set(released)
    )
    return MatchResult(
        release_photos=released,
        borderline_photos=borderline,
        cluster_id=winner.cluster_id,
        cluster_band=band,
        best_score=round(margin.best_score, 4),
        second_score=round(margin.second_score, 4),
        margin=round(margin.margin, 4),
        margin_ok=margin.ok,
        selfie_strong=selfie_strong,
        thresholds=thresholds,
        faces=decisions,
    )
