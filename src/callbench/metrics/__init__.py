"""KPIs, composite scoring and paired statistics."""

from __future__ import annotations

from .cost import PRICING, PRICING_AS_OF, CostBreakdown, LatencyBreakdown, cost, latency
from .kpis import PRIMARY_KPIS, SystemMetrics, aggregate, safety_failures
from .score import PENALTIES, WEIGHTS, ScoreBreakdown, score_case
from .stats import (
    Interval,
    bootstrap_ci,
    cohens_h,
    effect_label,
    mcnemar_exact,
    paired_counts,
    wilson_interval,
)
from .trust import TrustScore, trust_score

__all__ = [
    "PENALTIES",
    "PRICING",
    "PRICING_AS_OF",
    "CostBreakdown",
    "LatencyBreakdown",
    "TrustScore",
    "cost",
    "latency",
    "trust_score",
    "PRIMARY_KPIS",
    "WEIGHTS",
    "Interval",
    "ScoreBreakdown",
    "SystemMetrics",
    "aggregate",
    "bootstrap_ci",
    "cohens_h",
    "effect_label",
    "mcnemar_exact",
    "paired_counts",
    "safety_failures",
    "score_case",
    "wilson_interval",
]
