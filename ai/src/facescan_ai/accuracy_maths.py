"""Benchmark maths: precision, recall and an upper confidence bound on the false-release rate.

The bound is the exact one-sided Clopper-Pearson upper limit: the largest false-release rate p
for which seeing at most k false releases in n released photos still has probability >= 5%.
With k = 0 it is 1 - 0.05 ** (1 / n), about 3 / n ("rule of three"). Pure Python, no scipy.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Counts:
    """Released vs ground truth for one person or in total."""

    true_releases: int
    false_releases: int
    missed: int

    @property
    def released(self) -> int:
        """Photos released."""
        return self.true_releases + self.false_releases

    @property
    def truth(self) -> int:
        """Photos the person really appears in."""
        return self.true_releases + self.missed

    @property
    def precision(self) -> float | None:
        """Share of released photos that are correct (None when nothing was released)."""
        return self.true_releases / self.released if self.released else None

    @property
    def recall(self) -> float | None:
        """Share of true photos that were released (None when there are none)."""
        return self.true_releases / self.truth if self.truth else None

    def __add__(self, other: "Counts") -> "Counts":
        """Purpose: sum counts. Side effects: none."""
        return Counts(
            self.true_releases + other.true_releases,
            self.false_releases + other.false_releases,
            self.missed + other.missed,
        )


def compare(released: set[str], truth: set[str]) -> Counts:
    """Purpose: counts for one person from released and true photo ids. Side effects: none."""
    return Counts(len(released & truth), len(released - truth), len(truth - released))


def _binomial_cdf(k: int, n: int, p: float) -> float:
    """Purpose: P(X <= k) for X ~ Binomial(n, p), computed in log space. Side effects: none."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    terms = [
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * log_p
        + (n - i) * log_q
        for i in range(k + 1)
    ]
    top = max(terms)
    return min(1.0, math.exp(top) * sum(math.exp(t - top) for t in terms))


def false_release_upper_bound(
    false_releases: int, released: int, confidence: float = 0.95
) -> float:
    """
    Purpose: one-sided upper confidence bound on the true false-release rate.
    Inputs: false releases k, released photos n (k <= n), confidence in (0, 1).
    Outputs: bound in [0, 1] (1.0 when nothing was released). Side effects: none.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if released < 0 or not 0 <= false_releases <= released:
        raise ValueError("need 0 <= false_releases <= released")
    if released == 0 or false_releases == released:
        return 1.0
    alpha = 1.0 - confidence
    if false_releases == 0:
        return 1.0 - math.pow(alpha, 1.0 / released)
    low, high = false_releases / released, 1.0
    for _ in range(200):
        mid = (low + high) / 2
        if _binomial_cdf(false_releases, released, mid) > alpha:
            low = mid
        else:
            high = mid
    return high


def releases_needed(target_rate: float, confidence: float = 0.95) -> int:
    """
    Purpose: released photos needed, with zero false releases, before the upper bound drops
        to the target rate (e.g. 0.001 for a 99.9% precision claim).
    Outputs: n. Side effects: none.
    """
    if not 0.0 < target_rate < 1.0:
        raise ValueError("target_rate must be in (0, 1)")
    return math.ceil(math.log(1.0 - confidence) / math.log1p(-target_rate))


SIZE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<40px", 0.0, 40.0),
    ("40-80px", 40.0, 80.0),
    ("80-160px", 80.0, 160.0),
    (">=160px", 160.0, math.inf),
)
QUALITY_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<0.3", -math.inf, 0.3),
    ("0.3-0.5", 0.3, 0.5),
    ("0.5-0.7", 0.5, 0.7),
    (">=0.7", 0.7, math.inf),
)


def bucket_of(value: float, buckets: tuple[tuple[str, float, float], ...]) -> str:
    """Purpose: name of the bucket [low, high) that holds value. Side effects: none."""
    for name, low, high in buckets:
        if low <= value < high:
            return name
    return buckets[-1][0]
