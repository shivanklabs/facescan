"""Confidence bands that drive release rules (precision over recall, invariant 8).

- release: the photo may be shown to the guest.
- borderline: not released; listed for debugging and, later, for review.
- none: no match.
"""

from dataclasses import dataclass
from enum import StrEnum


class Band(StrEnum):
    """Match band of a cluster or a face."""

    RELEASE = "release"
    BORDERLINE = "borderline"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Cosine thresholds for the primary (AuraFace) and second-opinion (SFace) engines."""

    primary_release: float
    primary_borderline: float
    second_release: float
    second_borderline: float

    def __post_init__(self) -> None:
        """Purpose: borderline thresholds may not exceed release ones. Side effects: none."""
        if self.primary_borderline > self.primary_release:
            raise ValueError("primary borderline threshold exceeds release threshold")
        if self.second_borderline > self.second_release:
            raise ValueError("second borderline threshold exceeds release threshold")


def cluster_band(best_score: float, margin_ok: bool, thresholds: Thresholds) -> Band:
    """
    Purpose: band of the winning cluster from its prototype score and the margin check.
    Outputs: release only if the score passes and the margin is clear. Side effects: none.
    """
    if best_score >= thresholds.primary_release and margin_ok:
        return Band.RELEASE
    if best_score >= thresholds.primary_borderline:
        return Band.BORDERLINE
    return Band.NONE


def face_band(
    primary_score: float,
    second_score: float,
    cluster: Band,
    selfie_strong: bool,
    thresholds: Thresholds,
) -> Band:
    """
    Purpose: band of one face in the winning cluster (the per-face check).
        Release needs: a released cluster, a strong selfie, the primary engine above its
        release threshold and the second engine agreeing (above its release threshold).
        Otherwise a face scoring above the primary borderline threshold is borderline.
    Side effects: none.
    """
    if cluster is Band.NONE:
        return Band.NONE
    if (
        cluster is Band.RELEASE
        and selfie_strong
        and primary_score >= thresholds.primary_release
        and second_score >= thresholds.second_release
    ):
        return Band.RELEASE
    if primary_score >= thresholds.primary_borderline:
        return Band.BORDERLINE
    return Band.NONE
