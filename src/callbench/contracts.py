"""Typed contracts exchanged between the pipeline stages.

These dataclasses are the internal wire format. Every stage boundary in
``orchestration.pipeline`` is one of these types, which is what makes each
decision independently inspectable in the evaluation ledger.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum, StrEnum
from typing import Any


class Decision(StrEnum):
    """What the planner concluded the agent should do."""

    EXECUTE = "execute"
    CLARIFY = "clarify"
    REFUSE = "refuse"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SideEffect(StrEnum):
    """Declared side effect of a tool, used by the policy gate.

    ``NONE`` is read-only. ``DESTRUCTIVE`` covers operations whose inverse is
    not available in the simulator, and is what triggers the destructive-scope
    checks regardless of what the planner believed it was doing.
    """

    NONE = "none"
    CREATE = "create"
    MUTATE = "mutate"
    SEND = "send"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class Policy:
    """The execution policy handed to the agent as part of the task contract."""

    allow_external_side_effects: bool = False
    require_confirmation_for_destructive_actions: bool = True
    forbid_fabricated_identifiers: bool = True
    max_repair_attempts: int = 2
    max_tool_calls: int = 8

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Policy:
        known = {f: raw[f] for f in Policy.__dataclass_fields__ if f in raw}
        return Policy(**known)


@dataclass(frozen=True)
class TaskContract:
    """Stage 1 input: everything the agent is allowed to know about the task."""

    task_id: str
    user_request: str
    catalogue: str
    fixture: str
    current_time: str
    policy: Policy = field(default_factory=Policy)
    partition: str = "easy"
    difficulty_factors: tuple[str, ...] = ()


@dataclass
class TaskAnalysis:
    """Stage 2 output: the analyst's typed interpretation of the request.

    This is an internal contract between the reasoner and the planner, never a
    final answer, and the analyst is structurally forbidden from calling tools.
    """

    primary_intent: str
    target: dict[str, Any] = field(default_factory=dict)
    requested_effect: str = ""
    requires_existing_message: bool = False
    dependencies: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    execution_mode: str = "tool_chain"


@dataclass
class PlanStep:
    """One tool invocation.

    ``arguments`` may contain provenance references of the form
    ``$s1.results[0].thread_id``, resolved only after step ``s1`` returns. That
    deferral is the mechanism that makes fabricated identifiers detectable
    rather than merely unlikely.
    """

    step_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    decision: Decision = Decision.EXECUTE
    steps: list[PlanStep] = field(default_factory=list)
    clarification_question: str | None = None
    rationale: str = ""


@dataclass
class Violation:
    """A policy-gate or verifier finding, keyed to the error taxonomy."""

    code: str
    message: str
    step_id: str | None = None
    fatal: bool = True


@dataclass
class GuardVerdict:
    approved: bool = True
    violations: list[Violation] = field(default_factory=list)

    @property
    def fatal_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.fatal]


@dataclass
class ExecutionRecord:
    """One executed step, with the before/after state diff it produced."""

    step_id: str
    tool: str
    resolved_arguments: dict[str, Any]
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    before_hash: str = ""
    after_hash: str = ""
    changed_resources: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class LayerResult:
    name: str
    passed: bool
    detail: str = ""
    authoritative: bool = True


@dataclass
class Verdict:
    """Aggregate of the four verification layers plus the taxonomy codes."""

    passed: bool = False
    layers: list[LayerResult] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    unsafe: bool = False
    fabrication_count: int = 0

    def layer(self, name: str) -> LayerResult | None:
        return next((layer for layer in self.layers if layer.name == name), None)


@dataclass
class Attempt:
    """One pass through analyst -> planner -> guardian -> executor -> verifier."""

    index: int
    analysis: TaskAnalysis | None = None
    plan: Plan | None = None
    guard: GuardVerdict | None = None
    execution: list[ExecutionRecord] = field(default_factory=list)
    verdict: Verdict | None = None
    repair_reason: str | None = None


@dataclass
class CaseResult:
    """The replay artefact for a single benchmark case."""

    task_id: str
    partition: str
    system: str
    model: str
    attempts: list[Attempt] = field(default_factory=list)
    passed: bool = False
    #: Codes that decide this case: verification layers plus anything the gate
    #: blocked on the final attempt.
    error_codes: list[str] = field(default_factory=list)
    #: Every code raised across every attempt, including ones a repair later
    #: fixed. Diagnostic only — a repaired fault still happened, and hiding it
    #: would make the gate look like it had nothing to do.
    emitted_codes: list[str] = field(default_factory=list)
    #: True only when a safety-critical failure actually reached the mailbox.
    #: A blocked unsafe action is the architecture working, not an unsafe
    #: action, and counting it as one would penalise the guard for guarding.
    unsafe: bool = False
    fabrication_count: int = 0
    tool_calls: int = 0
    schema_valid_calls: int = 0
    emitted_calls: int = 0
    first_tool_correct: bool = False
    arguments_exact_match: bool = False
    plan_success: bool = False
    state_transition_ok: bool = False
    clarification_correct: bool | None = None
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    score: float = 0.0
    penalties: list[str] = field(default_factory=list)

    @property
    def final_attempt(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(json.dumps(asdict(self), default=_encode))
        return payload


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
