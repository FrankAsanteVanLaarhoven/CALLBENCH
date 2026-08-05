"""Cost and latency accounting.

Accuracy alone does not decide whether an architecture is deployable. A
configuration that buys four points of plan success with three extra model
calls and a doubled p95 is a different proposition from one that does not, and
a benchmark that reports only the four points hides the trade.

Two rules keep these numbers honest:

* **Prices are data, not code.** The table below is a cached snapshot with an
  explicit date. Pass ``--price-input``/``--price-output`` to override it
  rather than trusting a constant that ages silently.
* **A run with no model calls reports no cost.** The deterministic reference
  planner spends nothing; printing ``$0.0000`` for it would invite comparison
  against a model run, so it is reported as not applicable instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from ..contracts import CaseResult

#: USD per million tokens, cached 2026-06-24. Override rather than trust.
PRICING_AS_OF = "2026-06-24"
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass
class LatencyBreakdown:
    """Wall-clock, split by pipeline stage.

    Reported as median and p95 rather than mean: agent latency distributions
    are long-tailed by construction (a repair doubles a case), and a mean hides
    exactly the tail an operator cares about.
    """

    planning_ms: float = 0.0
    execution_ms: float = 0.0
    verification_ms: float = 0.0
    repair_ms: float = 0.0
    total_ms: float = 0.0
    p95_total_ms: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "planning_ms": round(self.planning_ms, 3),
            "execution_ms": round(self.execution_ms, 3),
            "verification_ms": round(self.verification_ms, 3),
            "repair_ms": round(self.repair_ms, 3),
            "total_ms": round(self.total_ms, 3),
            "p95_total_ms": round(self.p95_total_ms, 3),
        }


@dataclass
class CostBreakdown:
    model: str
    priced: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    usd_total: float = 0.0
    usd_per_case: float = 0.0
    usd_per_passed_case: float | None = None
    mean_repairs: float = 0.0
    mean_tool_calls: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "priced": self.priced,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd_total": round(self.usd_total, 6),
            "usd_per_case": round(self.usd_per_case, 6),
            "usd_per_passed_case": (
                round(self.usd_per_passed_case, 6)
                if self.usd_per_passed_case is not None
                else None
            ),
            "mean_repairs": round(self.mean_repairs, 3),
            "mean_tool_calls": round(self.mean_tool_calls, 3),
            "pricing_as_of": PRICING_AS_OF,
            "notes": self.notes,
        }


def latency(results: list[CaseResult]) -> LatencyBreakdown:
    if not results:
        return LatencyBreakdown()
    totals = sorted(r.latency_ms for r in results)
    index = max(0, min(len(totals) - 1, int(round(0.95 * (len(totals) - 1)))))
    return LatencyBreakdown(
        planning_ms=median(r.planning_ms for r in results),
        execution_ms=median(r.execution_ms for r in results),
        verification_ms=median(r.verification_ms for r in results),
        repair_ms=median(r.repair_ms for r in results),
        total_ms=median(totals),
        p95_total_ms=totals[index],
    )


def cost(
    model: str,
    results: list[CaseResult],
    *,
    price_input: float | None = None,
    price_output: float | None = None,
) -> CostBreakdown:
    breakdown = CostBreakdown(model=model)
    if not results:
        return breakdown

    breakdown.mean_repairs = sum(max(0, len(r.attempts) - 1) for r in results) / len(results)
    breakdown.mean_tool_calls = sum(r.tool_calls for r in results) / len(results)

    # Backends accumulate usage across a run, so the per-case figures are
    # running totals; the last case carries the total for that system.
    breakdown.input_tokens = max((r.input_tokens for r in results), default=0)
    breakdown.output_tokens = max((r.output_tokens for r in results), default=0)

    if breakdown.input_tokens == 0 and breakdown.output_tokens == 0:
        breakdown.notes.append(
            "no model tokens were consumed; this run has no monetary cost to report"
        )
        return breakdown

    rates = PRICING.get(model)
    if price_input is not None and price_output is not None:
        rates = (price_input, price_output)
        breakdown.notes.append("prices supplied on the command line")
    if rates is None:
        breakdown.notes.append(
            f"no cached price for {model!r} (table as of {PRICING_AS_OF}); "
            "pass --price-input/--price-output to price this run"
        )
        return breakdown

    breakdown.priced = True
    breakdown.usd_total = (
        breakdown.input_tokens / 1_000_000 * rates[0]
        + breakdown.output_tokens / 1_000_000 * rates[1]
    )
    breakdown.usd_per_case = breakdown.usd_total / len(results)
    passed = sum(1 for r in results if r.passed)
    breakdown.usd_per_passed_case = (breakdown.usd_total / passed) if passed else None
    return breakdown
