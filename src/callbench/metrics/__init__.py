"""KPIs, composite scoring and paired statistics."""

from __future__ import annotations

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

__all__ = [
    "PENALTIES",
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
