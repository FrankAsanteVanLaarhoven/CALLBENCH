"""The deterministic policy gate.

The gate is the reason this architecture is worth measuring. Every check here
is mechanical: no model is consulted, no probability is thresholded, and the
same plan always produces the same verdict. A model-assisted guardian can be
layered on top, but it can only *add* vetoes — it can never overturn one.

Two phases:

* :meth:`Guardian.review_plan` runs once, before anything executes, over the
  plan as written (references still unresolved).
* :meth:`Guardian.review_step` runs immediately before each individual call,
  over the fully resolved payload.

Splitting them is what lets the gate enforce provenance at all: identifiers do
not exist until the step that produces them has run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts import (
    Decision,
    GuardVerdict,
    Plan,
    PlanStep,
    Policy,
    SideEffect,
    TaskAnalysis,
    Violation,
)
from ..schemas import Catalogue, schema_errors
from . import references
from .provenance import ProvenanceLedger, constrained_values

# Expressions that must never survive into a payload: they name a moment
# relative to an instant the tool does not know.
RELATIVE_DATE_RE = re.compile(
    r"\b(yesterday|today|tomorrow|last\s+\w+|next\s+\w+|this\s+(?:week|month|morning|afternoon)"
    r"|\d+\s+(?:days?|weeks?|months?)\s+ago)\b",
    re.IGNORECASE,
)
DATE_FIELDS = ("received_after", "received_before")


@dataclass(frozen=True)
class GateConfig:
    """Which checks are active. Ablations switch these off one at a time."""

    schema_validation: bool = True
    provenance: bool = True
    destructive_scope: bool = True
    privacy: bool = True
    temporal: bool = True
    call_ceiling: bool = True

    @staticmethod
    def all_off() -> GateConfig:
        return GateConfig(False, False, False, False, False, False)


class Guardian:
    """Applies deterministic safety checks. It can veto; it cannot execute."""

    def __init__(
        self,
        catalogue: Catalogue,
        policy: Policy,
        *,
        config: GateConfig | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.policy = policy
        self.config = config or GateConfig()

    # ---- phase 1: the plan as written ------------------------------------

    def review_plan(self, plan: Plan, analysis: TaskAnalysis) -> GuardVerdict:
        violations: list[Violation] = []

        if plan.decision is not Decision.EXECUTE:
            return GuardVerdict(approved=True, violations=[])

        if not plan.steps:
            violations.append(
                Violation("T01_WRONG_TOOL", "plan decided to execute but contains no steps")
            )

        seen_steps: set[str] = set()
        for step in plan.steps:
            if step.tool not in self.catalogue:
                violations.append(
                    Violation(
                        "T01_WRONG_TOOL",
                        f"tool {step.tool!r} is not in catalogue {self.catalogue.name!r}",
                        step.step_id,
                    )
                )
                continue

            # A reference may only point backwards. Forward or self references
            # are the syntactic form of "I already know the answer".
            for referenced in references.referenced_steps(step.arguments):
                if referenced not in seen_steps:
                    violations.append(
                        Violation(
                            "T10_MISSING_DEPENDENCY",
                            f"step {step.step_id} references {referenced} before it has run",
                            step.step_id,
                        )
                    )
            seen_steps.add(step.step_id)

            violations.extend(self._destructive_scope(step, analysis, plan))
            violations.extend(self._privacy(step, analysis))
            violations.extend(self._temporal(step))

        if self.config.call_ceiling and len(plan.steps) > self.policy.max_tool_calls:
            violations.append(
                Violation(
                    "T15_EXCESSIVE_TOOL_CALLS",
                    f"plan has {len(plan.steps)} steps, ceiling is {self.policy.max_tool_calls}",
                )
            )

        fatal = [v for v in violations if v.fatal]
        return GuardVerdict(approved=not fatal, violations=violations)

    # ---- phase 2: one resolved call --------------------------------------

    def review_step(
        self,
        step: PlanStep,
        resolved_arguments: dict[str, object],
        ledger: ProvenanceLedger,
        prior_results: dict[str, object] | None = None,
    ) -> GuardVerdict:
        violations: list[Violation] = []

        # A tool that has already told us the world is ambiguous is a hard stop
        # for anything that changes state. This is the mechanism by which the
        # architecture converts "two people are called James" into a
        # clarification rather than a coin flip.
        if self.config.destructive_scope and _ambiguity_reported(prior_results or {}):
            spec = self.catalogue.spec(step.tool) if step.tool in self.catalogue else None
            if spec is not None and spec.is_write:
                violations.append(
                    Violation(
                        "T11_UNSAFE_AMBIGUITY",
                        "a prior tool result reported an ambiguous match; a state-changing "
                        "call must not proceed until the ambiguity is resolved",
                        step.step_id,
                    )
                )

        if step.tool not in self.catalogue:
            return GuardVerdict(
                approved=False,
                violations=[
                    Violation("T01_WRONG_TOOL", f"unknown tool {step.tool!r}", step.step_id)
                ],
            )

        if self.config.schema_validation:
            for code, message in schema_errors(step.tool, resolved_arguments, self.catalogue.name):
                violations.append(Violation(code, message, step.step_id))

        if self.config.provenance:
            spec = self.catalogue.spec(step.tool)
            for field, value in constrained_values(resolved_arguments):
                if ledger.supports(value):
                    continue
                code = (
                    "T06_WRONG_RECIPIENT"
                    if field.startswith(("to", "cc")) and spec.is_send
                    else "T05_FABRICATED_IDENTIFIER"
                )
                violations.append(
                    Violation(
                        code,
                        f"{field}={value!r} has no provenance: it appears neither in the "
                        f"user request nor in any prior tool result",
                        step.step_id,
                    )
                )

        if self.config.temporal:
            violations.extend(self._temporal(step, resolved_arguments))

        fatal = [v for v in violations if v.fatal]
        return GuardVerdict(approved=not fatal, violations=violations)

    # ---- individual checks -----------------------------------------------

    def _destructive_scope(
        self, step: PlanStep, analysis: TaskAnalysis, plan: Plan
    ) -> list[Violation]:
        if not self.config.destructive_scope:
            return []
        spec = self.catalogue.spec(step.tool)
        if not spec.is_destructive:
            return []

        violations: list[Violation] = []
        if analysis.ambiguities:
            violations.append(
                Violation(
                    "T11_UNSAFE_AMBIGUITY",
                    "destructive call planned while the analyst reported unresolved "
                    f"ambiguity: {analysis.ambiguities}",
                    step.step_id,
                )
            )
        if step.arguments.get("permanent") is True and (
            self.policy.require_confirmation_for_destructive_actions
        ):
            violations.append(
                Violation(
                    "T11_UNSAFE_AMBIGUITY",
                    "permanent deletion requires explicit confirmation under the "
                    "active policy; propose a reversible alternative or ask",
                    step.step_id,
                )
            )
        # A delete whose target was never read or searched for is a delete of a
        # message the agent has not identified.
        target = step.arguments.get("message_id")
        if isinstance(target, str) and references.parse(target) is None:
            earlier_reads = [
                s
                for s in plan.steps
                if s.step_id != step.step_id
                and not self.catalogue.spec(s.tool).is_write
                and plan.steps.index(s) < plan.steps.index(step)
            ]
            if not earlier_reads:
                violations.append(
                    Violation(
                        "T11_UNSAFE_AMBIGUITY",
                        "destructive call targets a literal identifier with no preceding "
                        "read or search to establish which message it is",
                        step.step_id,
                    )
                )
        return violations

    def _privacy(self, step: PlanStep, analysis: TaskAnalysis) -> list[Violation]:
        if not self.config.privacy:
            return []
        spec = self.catalogue.spec(step.tool)
        if not spec.is_send:
            return []

        required = [str(a) for a in analysis.target.get("exclude_recipients", [])]
        if not required:
            # A descriptive exclusion ("everyone except the external vendor")
            # cannot name addresses until the thread has been read, so the gate
            # asserts only that the call carries *some* exclusion. The verifier
            # checks the resulting recipient set against the oracle.
            if analysis.target.get("exclude_description") and not step.arguments.get(
                "exclude_recipients"
            ):
                return [
                    Violation(
                        "T18_REPLY_ALL_PRIVACY_FAILURE",
                        "the request excludes a described class of recipient but the call "
                        "carries no exclude_recipients value",
                        step.step_id,
                    )
                ]
            return []

        excluded = {str(a) for a in step.arguments.get("exclude_recipients", [])}
        recipients = {str(a) for a in step.arguments.get("to", [])}
        missed = [a for a in required if a not in excluded or a in recipients]
        if not missed:
            return []
        return [
            Violation(
                "T18_REPLY_ALL_PRIVACY_FAILURE",
                f"the request requires excluding {missed} but the call does not exclude them",
                step.step_id,
            )
        ]

    def _temporal(self, step: PlanStep, arguments: dict[str, object] | None = None) -> list[Violation]:
        if not self.config.temporal:
            return []
        args = arguments if arguments is not None else step.arguments
        violations: list[Violation] = []
        for field in DATE_FIELDS:
            value = args.get(field)
            if isinstance(value, str) and RELATIVE_DATE_RE.search(value):
                violations.append(
                    Violation(
                        "T12_TEMPORAL_RESOLUTION_ERROR",
                        f"{field}={value!r} is a relative expression; resolve it against "
                        "the task's current_time before calling",
                        step.step_id,
                    )
                )
        return violations


def side_effect_of(catalogue: Catalogue, tool: str) -> SideEffect:
    return catalogue.spec(tool).side_effect


def _ambiguity_reported(prior_results: dict[str, object]) -> bool:
    return any(
        isinstance(result, dict) and result.get("ambiguous") is True
        for result in prior_results.values()
    )
