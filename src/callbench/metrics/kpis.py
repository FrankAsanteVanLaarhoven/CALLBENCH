"""The seven primary KPIs, plus the operational counters.

Reported together, always. A single accuracy number cannot distinguish an agent
that produced a malformed payload from one that deleted the wrong message, and
the whole design of this benchmark is a refusal to make that number available.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..contracts import CaseResult
from ..datasets.task import Task
from ..taxonomy import SAFETY_CRITICAL
from .score import score_case
from .stats import Interval, bootstrap_ci, wilson_interval

PRIMARY_KPIS: tuple[str, ...] = (
    "tool_selection_accuracy",
    "argument_exact_match",
    "schema_validity_rate",
    "plan_success_rate",
    "state_transition_accuracy",
    "fabrication_rate",
    "unsafe_action_rate",
)


@dataclass
class SystemMetrics:
    system: str
    model: str
    n: int = 0
    rates: dict[str, Interval] = field(default_factory=dict)
    composite: Interval | None = None
    mean_tool_calls: float = 0.0
    mean_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    retry_rate: float = 0.0
    clarification_precision: float | None = None
    taxonomy: dict[str, int] = field(default_factory=dict)
    by_partition: dict[str, dict[str, float]] = field(default_factory=dict)
    #: task_id -> passed, retained so paired tests can be run across systems.
    outcomes: dict[str, bool] = field(default_factory=dict)
    unsafe_outcomes: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "model": self.model,
            "n": self.n,
            "rates": {k: v.as_tuple() for k, v in self.rates.items()},
            "composite": self.composite.as_tuple() if self.composite else None,
            "mean_tool_calls": self.mean_tool_calls,
            "mean_latency_ms": self.mean_latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retry_rate": self.retry_rate,
            "clarification_precision": self.clarification_precision,
            "taxonomy": self.taxonomy,
            "by_partition": self.by_partition,
        }


def aggregate(
    system: str, model: str, results: list[CaseResult], tasks: dict[str, Task]
) -> SystemMetrics:
    metrics = SystemMetrics(system=system, model=model, n=len(results))
    if not results:
        return metrics

    def rate(predicate) -> Interval:  # type: ignore[no-untyped-def]
        hits = sum(1 for r in results if predicate(r))
        return wilson_interval(hits, len(results))

    metrics.rates = {
        "tool_selection_accuracy": rate(lambda r: r.first_tool_correct),
        "argument_exact_match": rate(lambda r: r.arguments_exact_match),
        "schema_validity_rate": _schema_validity(results),
        "plan_success_rate": rate(lambda r: r.plan_success),
        "state_transition_accuracy": rate(lambda r: r.state_transition_ok),
        "fabrication_rate": rate(lambda r: r.fabrication_count > 0),
        "unsafe_action_rate": rate(lambda r: r.unsafe),
        "overall_pass_rate": rate(lambda r: r.passed),
    }

    scores = [score_case(r, tasks[r.task_id]).total for r in results if r.task_id in tasks]
    metrics.composite = bootstrap_ci(scores)

    metrics.mean_tool_calls = sum(r.tool_calls for r in results) / len(results)
    metrics.mean_latency_ms = sum(r.latency_ms for r in results) / len(results)
    metrics.input_tokens = max(r.input_tokens for r in results)
    metrics.output_tokens = max(r.output_tokens for r in results)
    metrics.retry_rate = sum(1 for r in results if len(r.attempts) > 1) / len(results)

    asked = [r for r in results if r.clarification_correct is not None]
    correct_asks = [
        r for r in asked if r.clarification_correct and tasks[r.task_id].oracle.decision == "clarify"
    ]
    metrics.clarification_precision = (len(correct_asks) / len(asked)) if asked else None

    counter: Counter[str] = Counter()
    for result in results:
        counter.update(result.error_codes)
    metrics.taxonomy = dict(sorted(counter.items()))

    partitions: dict[str, list[CaseResult]] = {}
    for result in results:
        partitions.setdefault(result.partition, []).append(result)
    metrics.by_partition = {
        name: {
            "n": float(len(group)),
            "pass_rate": sum(1 for r in group if r.passed) / len(group),
            "unsafe_rate": sum(1 for r in group if r.unsafe) / len(group),
            "state_transition_accuracy": sum(1 for r in group if r.state_transition_ok) / len(group),
        }
        for name, group in sorted(partitions.items())
    }

    metrics.outcomes = {r.task_id: r.passed for r in results}
    metrics.unsafe_outcomes = {r.task_id: r.unsafe for r in results}
    return metrics


def _schema_validity(results: list[CaseResult]) -> Interval:
    """Fraction of *emitted payloads* that conform, not fraction of tasks.

    Reported per payload because a task that emits four calls and gets one
    wrong is not 0% schema-valid, and a benchmark that says so will mislead
    anyone tuning a decoder.
    """
    emitted = sum(r.emitted_calls for r in results)
    valid = sum(r.schema_valid_calls for r in results)
    return wilson_interval(valid, emitted) if emitted else wilson_interval(0, 0)


def safety_failures(results: list[CaseResult]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for result in results:
        counter.update(code for code in result.error_codes if code in SAFETY_CRITICAL)
    return dict(sorted(counter.items()))
