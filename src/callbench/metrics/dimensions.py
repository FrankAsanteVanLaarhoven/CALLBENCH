"""The eight evaluation dimensions.

Eight tables are the right artefact for diagnosis and the wrong one for
comparison. This is the comparison view: one row per dimension, one column per
system, with the run-level dimensions stated once.

============  =========================  ===============================
Dimension     Metric                     Scope
============  =========================  ===============================
Correctness   Pass rate                  per system
Safety        Unsafe action rate         per system
Reliability   Behavioural Stability      per run (the simulator)
Robustness    Pass rate under mutation   per system, needs a mutation run
Efficiency    Median latency             per system
Cost          Tokens and tool calls      per system
Reproducibility  Replay component match  per run (the tree)
Provenance    Fabrication rate           per system
============  =========================  ===============================

Two dimensions are deliberately **run-level**, not per-system: behavioural
stability is a property of the simulator, and reproducibility is a property of
the tree. Reporting them per system would imply a system could be individually
more reproducible than another, which is not a thing.

Dimensions that were not measured report ``None`` rather than a default. A
zero would read as a measurement, and an unmeasured dimension is not a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .kpis import SystemMetrics


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    metric: str
    higher_is_better: bool
    unit: str = "%"
    scope: str = "system"


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("correctness", "Correctness", "Pass rate", True),
    Dimension("safety", "Safety", "Unsafe action rate", False),
    Dimension("reliability", "Reliability", "Behavioural Stability", True, scope="run"),
    Dimension("robustness", "Robustness", "Pass rate under mutation", True),
    Dimension("efficiency", "Efficiency", "Median latency", False, unit="ms"),
    Dimension("cost", "Cost", "Mean tool calls", False, unit=""),
    Dimension("reproducibility", "Reproducibility", "Replay component match", True, scope="run"),
    Dimension("provenance", "Provenance", "Fabrication rate", False),
)


@dataclass
class DimensionRow:
    dimension: Dimension
    values: dict[str, float | None]
    run_level: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dimension": self.dimension.label,
            "metric": self.dimension.metric,
            "unit": self.dimension.unit,
            "higher_is_better": self.dimension.higher_is_better,
            "scope": self.dimension.scope,
        }
        if self.dimension.scope == "run":
            payload["value"] = self.run_level
        else:
            payload["by_system"] = self.values
        return payload


def build(
    systems: list[SystemMetrics],
    *,
    behavioural_stability: float | None = None,
    replay_match: float | None = None,
    generalisation: dict[str, float] | None = None,
) -> list[DimensionRow]:
    """Assemble the comparison table.

    ``generalisation`` maps system name to the *absolute* pass rate under
    semantics-preserving mutation — not retention. Retention is a ratio to each
    system's own baseline, so a system that barely consults the catalogue posts
    a high retention while performing terribly; using it here would rank the
    weakest system as the most robust. Omit the argument when no mutation run
    accompanied this benchmark, and the row reports "not measured" rather than
    inventing a number.
    """
    rows: list[DimensionRow] = []
    for dimension in DIMENSIONS:
        values: dict[str, float | None] = {}
        run_level: float | None = None

        if dimension.key == "correctness":
            values = {m.system: _rate(m, "overall_pass_rate") for m in systems}
        elif dimension.key == "safety":
            values = {m.system: _rate(m, "unsafe_action_rate") for m in systems}
        elif dimension.key == "provenance":
            values = {m.system: _rate(m, "fabrication_rate") for m in systems}
        elif dimension.key == "efficiency":
            values = {
                m.system: (m.latency.total_ms if m.latency else None) for m in systems
            }
        elif dimension.key == "cost":
            values = {m.system: m.mean_tool_calls for m in systems}
        elif dimension.key == "robustness":
            values = {m.system: (generalisation or {}).get(m.system) for m in systems}
        elif dimension.key == "reliability":
            run_level = behavioural_stability
        elif dimension.key == "reproducibility":
            run_level = replay_match

        rows.append(DimensionRow(dimension, values, run_level))
    return rows


def to_dict(rows: list[DimensionRow]) -> list[dict[str, Any]]:
    return [row.to_dict() for row in rows]


def _rate(metrics: SystemMetrics, key: str) -> float | None:
    interval = metrics.rates.get(key)
    return interval.point * 100 if interval else None
