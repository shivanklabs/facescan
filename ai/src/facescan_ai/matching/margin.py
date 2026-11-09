"""Best-vs-second-best margin check: the winning cluster must clearly beat the runner-up."""

from dataclasses import dataclass

import numpy as np

from facescan_ai.types import FloatArray


@dataclass(frozen=True, slots=True)
class MarginResult:
    """Outcome of the margin check over cluster scores."""

    best_index: int | None
    best_score: float
    second_score: float
    margin: float
    ok: bool


def margin_check(scores: FloatArray, min_margin: float) -> MarginResult:
    """
    Purpose: find the best and second-best scores and check their gap.
    Inputs: one score per cluster, minimum margin. With a single cluster the runner-up
        score is -1 (no competitor). With no clusters the result is not ok.
    Outputs: MarginResult. Side effects: none.
    """
    if min_margin < 0:
        raise ValueError("min_margin must be >= 0")
    if scores.shape[0] == 0:
        return MarginResult(None, -1.0, -1.0, 0.0, False)
    order = np.argsort(-scores, kind="stable")
    best = float(scores[order[0]])
    second = float(scores[order[1]]) if scores.shape[0] > 1 else -1.0
    margin = best - second
    return MarginResult(int(order[0]), best, second, margin, margin >= min_margin)
