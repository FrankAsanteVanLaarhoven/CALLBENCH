"""Factorial decomposition: how much is the architecture, how much the planner?

The obvious objection to any near-ceiling result is that the ceiling belongs to
the planner rather than to the harness around it. That objection is answerable
by measurement, not by framing.

This module crosses the two axes explicitly:

* **Planner competence** — for the reference backend, the three profiles
  (``guessing``, ``shallow``, ``full``). For a model backend, the model itself
  is the planner axis and this grid runs one row per model.
* **Architecture** — the full pipeline, the pipeline with the deterministic
  gate removed, and the pipeline with the analyst removed as well.

Reporting the grid rather than a single cell makes three quantities visible
that a single number hides:

``planner main effect``
    mean outcome per planner, averaged over architectures.
``architecture main effect``
    mean outcome per architecture, averaged over planners.
``interaction``
    how much the architecture's benefit *depends on* planner competence. A
    large positive interaction is the interesting case: it means the
    architecture is worth most exactly where the planner is weakest, which is
    the deployment regime that matters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .contracts import CaseResult
from .datasets.task import Task
from .models.base import Backend
from .orchestration.config import BY_NAME, SystemConfig

#: Architecture axis, weakest to strongest, chosen so that **each step adds
#: exactly one capability**:
#:
#: 1. ``structured_outputs`` — planner only, no analyst, no gate, no repair.
#: 2. ``multi_agent_no_hooks`` — adds the contract analyst.
#: 3. ``callbench_full`` — adds the deterministic gate and bounded repair.
#:
#: ``single_agent_planner`` is deliberately excluded: with the gate off it
#: differs from ``multi_agent_no_hooks`` only in repair budget, and repairs
#: never fire when nothing blocks. Including it would put two identical columns
#: in the grid and make the architecture axis look flatter than it is.
ARCHITECTURES: tuple[str, ...] = (
    "structured_outputs",
    "multi_agent_no_hooks",
    "callbench_full",
)

#: Planner axis for the deterministic backend.
REFERENCE_PROFILES: tuple[str, ...] = ("guessing", "shallow", "full")


@dataclass
class Cell:
    planner: str
    architecture: str
    n: int
    pass_rate: float
    unsafe_rate: float
    fabrication_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner": self.planner,
            "architecture": self.architecture,
            "n": self.n,
            "pass_rate": round(self.pass_rate, 4),
            "unsafe_rate": round(self.unsafe_rate, 4),
            "fabrication_rate": round(self.fabrication_rate, 4),
        }


@dataclass
class Decomposition:
    metric: str = "pass_rate"
    cells: list[Cell] = field(default_factory=list)

    # ---- marginals --------------------------------------------------------

    def _value(self, cell: Cell) -> float:
        return float(getattr(cell, self.metric))

    def planners(self) -> list[str]:
        return sorted({c.planner for c in self.cells}, key=_planner_order)

    def architectures(self) -> list[str]:
        return sorted({c.architecture for c in self.cells}, key=ARCHITECTURES.index)

    def cell(self, planner: str, architecture: str) -> Cell | None:
        return next(
            (c for c in self.cells if c.planner == planner and c.architecture == architecture),
            None,
        )

    def planner_effect(self) -> dict[str, float]:
        return {
            planner: _mean([self._value(c) for c in self.cells if c.planner == planner])
            for planner in self.planners()
        }

    def architecture_effect(self) -> dict[str, float]:
        return {
            arch: _mean([self._value(c) for c in self.cells if c.architecture == arch])
            for arch in self.architectures()
        }

    def architecture_span(self) -> float:
        """Best architecture minus worst, averaged over planners."""
        effects = self.architecture_effect()
        return max(effects.values()) - min(effects.values()) if effects else 0.0

    def planner_span(self) -> float:
        effects = self.planner_effect()
        return max(effects.values()) - min(effects.values()) if effects else 0.0

    def interaction(self) -> float:
        """Architecture benefit for the weakest planner minus that for the strongest.

        Positive means the architecture matters *more* when the planner is
        worse — which is the claim a safety architecture ought to be making,
        and the one a single strong-planner cell cannot support.
        """
        planners = self.planners()
        archs = self.architectures()
        if len(planners) < 2 or len(archs) < 2:
            return 0.0
        weak, strong = planners[0], planners[-1]
        low, high = archs[0], archs[-1]

        def gain(planner: str) -> float:
            a = self.cell(planner, low)
            b = self.cell(planner, high)
            return (self._value(b) - self._value(a)) if a and b else 0.0

        return gain(weak) - gain(strong)

    def attribution(self) -> dict[str, Any]:
        """The reviewer's question, answered in one object."""
        arch = self.architecture_span()
        planner = self.planner_span()
        total = arch + planner
        return {
            "architecture_span": round(arch, 4),
            "planner_span": round(planner, 4),
            "architecture_share": round(arch / total, 4) if total else None,
            "interaction": round(self.interaction(), 4),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "cells": [c.to_dict() for c in self.cells],
            "planner_effect": {k: round(v, 4) for k, v in self.planner_effect().items()},
            "architecture_effect": {
                k: round(v, 4) for k, v in self.architecture_effect().items()
            },
            "attribution": self.attribution(),
        }


def run(
    tasks: list[Task],
    backend_factory: Callable[[str, SystemConfig], Backend],
    *,
    planners: tuple[str, ...] = REFERENCE_PROFILES,
    architectures: tuple[str, ...] = ARCHITECTURES,
    metric: str = "pass_rate",
    progress: Callable[[str, str], None] | None = None,
) -> Decomposition:
    """Run every planner against every architecture on the same tasks."""
    from .orchestration.pipeline import Pipeline

    decomposition = Decomposition(metric=metric)
    for planner in planners:
        for architecture in architectures:
            if progress is not None:
                progress(planner, architecture)
            system = BY_NAME[architecture]
            backend = backend_factory(planner, system)
            pipeline = Pipeline(backend, system)
            results: list[CaseResult] = [pipeline.run(task) for task in tasks]
            decomposition.cells.append(
                Cell(
                    planner=planner,
                    architecture=architecture,
                    n=len(results),
                    pass_rate=_rate(results, lambda r: r.passed),
                    unsafe_rate=_rate(results, lambda r: r.unsafe),
                    fabrication_rate=_rate(results, lambda r: r.fabrication_count > 0),
                )
            )
    return decomposition


def reference_factory(planner: str, system: SystemConfig) -> Backend:
    """Pin the planner profile, overriding whatever the architecture declares.

    The architecture's own ``reference_profile`` is what makes the baseline
    ladder a ladder. Here it must be *held fixed* instead, or the two axes
    would move together and the decomposition would measure nothing.
    """
    from .models.reference import Profile, ReferenceBackend, ReferenceConfig

    return ReferenceBackend(
        ReferenceConfig(profile=Profile(planner), strict_json=system.strict_json)
    )


def _rate(results: list[CaseResult], predicate: Callable[[CaseResult], bool]) -> float:
    return (sum(1 for r in results if predicate(r)) / len(results)) if results else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _planner_order(planner: str) -> tuple[int, str]:
    return (
        REFERENCE_PROFILES.index(planner) if planner in REFERENCE_PROFILES else 99,
        planner,
    )
