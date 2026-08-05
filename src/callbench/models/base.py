"""The backend abstraction.

A *backend* supplies the three model-dependent decisions in the pipeline: the
typed analysis, the plan, and the bounded repair. Everything else — the policy
gate, the executor, the four verification layers, the metrics — is
model-independent by construction, which is what allows the same harness to
measure an architecture (holding the backend fixed) or a model (holding the
architecture fixed).

Two backends ship with the benchmark:

* :mod:`callbench.models.reference` — a deterministic, rule-based planner with
  no network dependency. Used for architecture ablations and CI.
* :mod:`callbench.models.anthropic_backend` — a Claude backend using structured
  outputs. Used for model comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..contracts import Plan, TaskAnalysis, TaskContract
from ..schemas import Catalogue


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    latency_ms: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.latency_ms += other.latency_ms


@dataclass
class RepairRequest:
    """What the failure analyst hands back to the planner.

    ``forbidden_changes`` encodes the never-retry rules: a repair may fix a
    schema error or a missing dependency, but it may not quietly change who a
    message is going to. Those changes are refused rather than re-approved.
    """

    reason: str
    violations: list[str] = field(default_factory=list)
    forbidden_changes: tuple[str, ...] = (
        "recipient_set",
        "deletion_scope",
        "forwarded_content",
        "reply_all_membership",
        "attachment_set",
    )


@runtime_checkable
class Backend(Protocol):
    """The model-dependent half of the agent."""

    name: str
    usage: Usage

    def analyse(self, contract: TaskContract, catalogue: Catalogue) -> TaskAnalysis: ...

    def plan(
        self, contract: TaskContract, catalogue: Catalogue, analysis: TaskAnalysis
    ) -> Plan: ...

    def repair(
        self,
        contract: TaskContract,
        catalogue: Catalogue,
        analysis: TaskAnalysis,
        previous: Plan,
        request: RepairRequest,
    ) -> Plan: ...

    def judge(
        self,
        contract: TaskContract,
        transcript: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        """Advisory semantic judgement.

        Never authoritative. Its verdict is recorded alongside the deterministic
        layers and is excluded from pass/fail by default (see
        ``verification.layers``): a model that grades its own homework is not an
        oracle.
        """
        ...


ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primary_intent": {
            "type": "string",
            "enum": [
                "reply", "forward", "draft", "send", "search", "read",
                "archive", "delete", "label", "mark_read", "summarise", "unknown",
            ],
        },
        "target": {
            "type": "object",
            "properties": {
                "sender": {"type": "string"},
                "topic": {"type": "string"},
                "selection": {"type": "string", "enum": ["latest", "oldest", "all", "specific"]},
                "exclude_recipients": {"type": "array", "items": {"type": "string"}},
                "include_all_recipients": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "requested_effect": {"type": "string"},
        "requires_existing_message": {"type": "boolean"},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "execution_mode": {
            "type": "string",
            "enum": ["direct_call", "tool_chain", "clarification", "refusal"],
        },
    },
    "required": [
        "primary_intent", "target", "requested_effect", "requires_existing_message",
        "dependencies", "ambiguities", "risk_level", "execution_mode",
    ],
    "additionalProperties": False,
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["execute", "clarify", "refuse"]},
        "rationale": {"type": "string"},
        "clarification_question": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["step_id", "tool", "arguments"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "rationale", "steps"],
    "additionalProperties": False,
}
