"""Statistics, in pure Python.

Every configuration sees the same task fixtures, so the comparisons are paired
and the appropriate test is McNemar's, not a two-sample proportion test. The
exact binomial form is used rather than the chi-squared approximation because
safety-failure counts are small by design and the approximation is unreliable
there — which is exactly the regime where a benchmark claim would be made.

Wilson intervals are used for rates rather than the normal approximation for
the same reason: at a true rate near zero, the normal interval includes
negative rates, and "unsafe action rate: -0.4%" is not a publishable number.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.point, self.low, self.high)

    def __str__(self) -> str:
        return f"{self.point:.3f} [{self.low:.3f}, {self.high:.3f}]"


def wilson_interval(successes: int, total: int, *, z: float = 1.959963985) -> Interval:
    """Score interval for a binomial proportion."""
    if total == 0:
        return Interval(0.0, 0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return Interval(p, max(0.0, centre - margin), min(1.0, centre + margin))


def bootstrap_ci(
    values: list[float], *, iterations: int = 2000, alpha: float = 0.05, seed: int = 20260805
) -> Interval:
    """Percentile bootstrap CI for a mean. Seeded, therefore reproducible."""
    if not values:
        return Interval(0.0, 0.0, 0.0)
    point = sum(values) / len(values)
    if len(values) == 1:
        return Interval(point, point, point)
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return Interval(point, lo, hi)


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value for paired binary outcomes.

    ``only_a`` counts cases where A succeeded and B failed; ``only_b`` the
    reverse. Concordant pairs carry no information and are excluded, which is
    the whole point of pairing.
    """
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return float(min(1.0, 2 * tail))


def paired_counts(a: dict[str, bool], b: dict[str, bool]) -> tuple[int, int, int, int]:
    """(both, only_a, only_b, neither) over the shared task ids."""
    shared = a.keys() & b.keys()
    both = sum(1 for t in shared if a[t] and b[t])
    only_a = sum(1 for t in shared if a[t] and not b[t])
    only_b = sum(1 for t in shared if b[t] and not a[t])
    neither = len(shared) - both - only_a - only_b
    return both, only_a, only_b, neither


def cohens_h(p1: float, p2: float) -> float:
    """Effect size for the difference between two proportions."""
    def phi(p: float) -> float:
        return 2 * math.asin(math.sqrt(min(max(p, 0.0), 1.0)))

    return phi(p1) - phi(p2)


def effect_label(h: float) -> str:
    magnitude = abs(h)
    if magnitude < 0.2:
        return "negligible"
    if magnitude < 0.5:
        return "small"
    if magnitude < 0.8:
        return "medium"
    return "large"
