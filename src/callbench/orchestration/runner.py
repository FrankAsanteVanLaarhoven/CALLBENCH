"""The benchmark runner.

Holds the experimental design: every system sees the *same* task fixtures in
the *same* order, so every cross-system comparison is paired and McNemar's test
is the right instrument. Anything that would break the pairing — sampling a
different subset per system, reordering, or reusing a mutated mailbox — is a
bug, not a tuning knob.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import repro, stability
from ..contracts import CaseResult
from ..datasets.task import Task
from ..metrics import (
    SystemMetrics,
    aggregate,
    cohens_h,
    dimensions,
    effect_label,
    mcnemar_exact,
    paired_counts,
)
from ..models import build_backend
from ..models.base import Backend
from .config import INAPPLICABLE_TO_REFERENCE, SystemConfig
from .pipeline import Pipeline

ProgressFn = Callable[[str, int, int], None]


@dataclass
class Comparison:
    baseline: str
    system: str
    metric: str
    baseline_rate: float
    system_rate: float
    only_baseline: int
    only_system: int
    p_value: float
    effect: float
    effect_size: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class RunReport:
    model: str
    partitions: list[str]
    systems: list[SystemMetrics] = field(default_factory=list)
    comparisons: list[Comparison] = field(default_factory=list)
    results: dict[str, list[CaseResult]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    #: Interpretation warnings raised by the runner itself, so that the most
    #: likely misreading of the table is answered on the page rather than left
    #: to the reader.
    notes: list[str] = field(default_factory=list)
    #: Component hashes and the replay id. A run without one cannot be checked
    #: for reproducibility, so it is always populated.
    fingerprint: dict[str, Any] = field(default_factory=dict)
    #: The eight-dimension comparison view, assembled after aggregation.
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    #: Behavioural Stability of the simulator at run time, 0-100.
    behavioural_stability: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def synthetic(self) -> bool:
        """True when the planner is the deterministic reference, not a model."""
        return self.model.startswith("reference")

    def to_dict(self, *, include_cases: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "meta": self.meta,
            "model": self.model,
            "synthetic_planner": self.synthetic,
            "partitions": self.partitions,
            "systems": [m.to_dict() for m in self.systems],
            "comparisons": [c.to_dict() for c in self.comparisons],
            "skipped_systems": self.skipped,
            "notes": self.notes,
            "reproducibility": self.fingerprint,
            "dimensions": self.dimensions,
            "behavioural_stability": self.behavioural_stability,
        }
        if include_cases:
            payload["cases"] = {
                system: [r.to_json() for r in results] for system, results in self.results.items()
            }
        return payload


class Runner:
    def __init__(
        self,
        model: str,
        systems: list[SystemConfig],
        *,
        effort: str = "high",
        progress: ProgressFn | None = None,
        dataset_root: Path | None = None,
        seed: int | None = None,
        price_input: float | None = None,
        price_output: float | None = None,
    ) -> None:
        self.model = model
        self.systems = systems
        self.effort = effort
        self.progress = progress
        self.dataset_root = dataset_root
        self.seed = seed
        self.price_input = price_input
        self.price_output = price_output
        self._shared_backend: Backend | None = None

    def _backend_for(self, system: SystemConfig) -> Backend:
        if self.model.startswith("reference"):
            # The reference planner's competence is part of the system under
            # test, so each configuration gets its own instance.
            from ..models.reference import Profile, ReferenceBackend, ReferenceConfig

            return ReferenceBackend(
                ReferenceConfig(
                    profile=Profile(system.reference_profile),
                    strict_json=system.strict_json,
                )
            )
        if self._shared_backend is None:
            self._shared_backend = build_backend(self.model, effort=self.effort)
        return self._shared_backend

    def run(self, tasks: list[Task], partitions: list[str]) -> RunReport:
        report = RunReport(
            model=self.model,
            partitions=partitions,
            meta={
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "task_count": len(tasks),
                "systems": [s.name for s in self.systems],
                "effort": self.effort,
            },
        )
        index = {task.id: task for task in tasks}

        for system in self.systems:
            if self.model.startswith("reference") and system.name in INAPPLICABLE_TO_REFERENCE:
                report.skipped[system.name] = (
                    "the deterministic reference planner does not consume tool descriptions, "
                    "so this ablation has no observable effect; run it against a model backend"
                )
                continue

            backend = self._backend_for(system)
            pipeline = Pipeline(backend, system)
            results: list[CaseResult] = []
            for position, task in enumerate(tasks, start=1):
                results.append(pipeline.run(task))
                if self.progress is not None:
                    self.progress(system.name, position, len(tasks))
            report.results[system.name] = results
            report.systems.append(
                aggregate(
                    system.name,
                    backend.name,
                    results,
                    index,
                    price_input=self.price_input,
                    price_output=self.price_output,
                )
            )

        report.comparisons = self._compare(report)
        report.notes = self._notes(report)
        stability_report = stability.measure()
        report.behavioural_stability = (
            stability_report.score if stability_report is not None else None
        )
        report.dimensions = dimensions.to_dict(
            dimensions.build(
                report.systems,
                behavioural_stability=report.behavioural_stability,
                # A fresh run always matches the tree it just ran on; the
                # meaningful check is `callbench replay` against a *recorded*
                # run, so this is 100 by construction and labelled as such.
                replay_match=100.0,
            )
        )
        report.fingerprint = repro.fingerprint(
            model=self.model,
            systems=self.systems,
            partitions=partitions,
            dataset_root=self.dataset_root,
            seed=self.seed,
            effort=self.effort,
        ).to_dict()
        return report

    @staticmethod
    def _notes(report: RunReport) -> list[str]:
        """Flag ablations that produced no signal, and say why that is not a finding.

        An ablation removes a *detector*. If the planner under test never
        commits the fault that detector catches, removing it changes nothing —
        and the honest reading is "this run cannot measure that component",
        not "that component does nothing". Leaving a row of p = 1.0 unexplained
        invites the second reading.
        """
        null_ablations = sorted(
            {
                c.system
                for c in report.comparisons
                if c.system.startswith("ablate_")
                and c.metric == "overall_pass_rate"
                and c.only_baseline == 0
                and c.only_system == 0
            }
        )
        if not null_ablations:
            return []
        return [
            "No-signal ablations: "
            + ", ".join(null_ablations)
            + ". These components detect faults the planner in this run never committed, so "
            "removing them changed nothing. That is a limitation of this run, not evidence "
            "the components are inert — the baseline ladder shows the same detectors firing "
            "heavily against planners that do commit those faults."
        ]

    def _compare(self, report: RunReport) -> list[Comparison]:
        """Paired McNemar tests of every system against the full architecture."""
        by_name = {m.system: m for m in report.systems}
        reference = by_name.get("callbench_full")
        if reference is None:
            return []

        comparisons: list[Comparison] = []
        for metrics in report.systems:
            if metrics.system == reference.system:
                continue
            for metric, full_map, other_map, higher_is_better in (
                ("overall_pass_rate", reference.outcomes, metrics.outcomes, True),
                ("unsafe_action_rate", reference.unsafe_outcomes, metrics.unsafe_outcomes, False),
            ):
                _, only_full, only_other, _ = paired_counts(full_map, other_map)
                full_rate = _rate(full_map)
                other_rate = _rate(other_map)
                comparisons.append(
                    Comparison(
                        baseline=reference.system,
                        system=metrics.system,
                        metric=metric,
                        baseline_rate=full_rate,
                        system_rate=other_rate,
                        only_baseline=only_full,
                        only_system=only_other,
                        p_value=mcnemar_exact(only_full, only_other),
                        effect=(
                            cohens_h(full_rate, other_rate)
                            if higher_is_better
                            else cohens_h(other_rate, full_rate)
                        ),
                        effect_size=effect_label(cohens_h(full_rate, other_rate)),
                    )
                )
        return comparisons


def _rate(outcomes: dict[str, bool]) -> float:
    return (sum(1 for v in outcomes.values() if v) / len(outcomes)) if outcomes else 0.0
