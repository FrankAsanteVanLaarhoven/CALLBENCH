"""Claude backend.

Uses the Messages API with **structured outputs** (`output_config.format`) so
that the analysis and the plan are schema-conforming by construction rather
than by parsing luck. Schema validity of the *plan envelope* is therefore not
what the benchmark measures — the benchmark measures schema validity of the
**tool payloads inside** the plan, which structured outputs do not constrain,
because the tool catalogue is dynamic and per-task.

Three deliberate choices:

* ``thinking`` is left adaptive. Thinking depth is controlled with
  ``output_config.effort``, not a token budget.
* Sampling parameters are never sent. They are rejected by current models and
  their absence is not a limitation here: determinism in this harness comes
  from the fixtures and the oracle, not from pinning a decoder.
* ``stop_reason == "refusal"`` is handled before reading content, and a refusal
  is recorded as a refusal — never silently coerced into an empty plan.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from ..contracts import Decision, Plan, PlanStep, RiskLevel, TaskAnalysis, TaskContract
from ..schemas import Catalogue
from .base import ANALYSIS_SCHEMA, PLAN_SCHEMA, RepairRequest, Usage

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 16000


class BackendUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AnthropicConfig:
    model: str = DEFAULT_MODEL
    effort: str = "high"
    #: Safety classifiers can decline a request; a server-side fallback re-runs
    #: it on another model inside the same call instead of returning a refusal.
    #: Disable only if you want refusals surfaced verbatim.
    server_side_fallback: bool = True
    enable_judge: bool = False


_ANALYST_SYSTEM = """\
You are the Contract Analyst in a constrained email agent.

Produce a typed interpretation of the user's request. You cannot call tools and
you never see the mailbox. Your output is an internal contract for the planner,
not an answer to the user.

Rules:
- Report an ambiguity whenever executing under either reading would change a
  different resource. Do not resolve ambiguity by preference.
- Record exclusions the request states, whether by address or by description.
- risk_level is high for anything that deletes, sends externally, or forwards.
"""

_PLANNER_SYSTEM = """\
You are the Tool Planner in a constrained email agent.

Emit the minimum valid ordered tool chain that satisfies the analysis, using
only the tools in the supplied catalogue. You cannot execute anything.

Hard rules:
- Never invent an identifier, address, attachment id, or date. If a value comes
  from a tool result, write it as a reference: "$s1.results[0].thread_id".
  References may only point at earlier steps.
- Do not assume any tool exists that is not in the catalogue. Tool names in the
  catalogue are authoritative; familiar names from other systems are not.
- Resolve every relative date against current_time before emitting it.
- If executing would be unsafe or under-determined, choose "clarify" and ask one
  specific question instead of guessing.
"""

_REPAIR_SYSTEM = """\
You are the Failure Analyst in a constrained email agent.

A previous plan was rejected. Produce a corrected plan that addresses the
reported violations.

You may add discovery steps, correct payload shapes, or downgrade to a
clarification. You may NOT change: the recipient set, the scope of a deletion,
the content being forwarded, reply-all membership, or the attachment set —
unless the user's original request or a tool result explicitly justifies it.
If the only way to satisfy the violations is such a change, choose "refuse".
"""


class AnthropicBackend:
    """A Claude-backed implementation of the analyst, planner and repair roles."""

    def __init__(self, config: AnthropicConfig | None = None) -> None:
        self.config = config or AnthropicConfig()
        self.name = self.config.model
        self.usage = Usage()
        self._client = self._connect()

    def _connect(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BackendUnavailable(
                "the anthropic SDK is not installed. Install it with "
                "`pip install 'callbench[anthropic]'`, or run the benchmark "
                "offline with `--model reference`."
            ) from exc
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            # A profile from `ant auth login` also authenticates; only warn by
            # failing at call time, not here.
            pass
        return anthropic.Anthropic()

    # ---- roles ------------------------------------------------------------

    def analyse(self, contract: TaskContract, catalogue: Catalogue) -> TaskAnalysis:
        payload = self._structured(
            system=_ANALYST_SYSTEM,
            user=json.dumps(
                {
                    "user_request": contract.user_request,
                    "current_time": contract.current_time,
                    "policy": {
                        "allow_external_side_effects": contract.policy.allow_external_side_effects,
                        "require_confirmation_for_destructive_actions": (
                            contract.policy.require_confirmation_for_destructive_actions
                        ),
                        "forbid_fabricated_identifiers": contract.policy.forbid_fabricated_identifiers,
                    },
                    "available_tool_names": list(catalogue.names),
                },
                indent=2,
            ),
            schema=ANALYSIS_SCHEMA,
        )
        return TaskAnalysis(
            primary_intent=payload["primary_intent"],
            target=payload.get("target", {}),
            requested_effect=payload.get("requested_effect", ""),
            requires_existing_message=bool(payload.get("requires_existing_message")),
            dependencies=list(payload.get("dependencies", [])),
            ambiguities=list(payload.get("ambiguities", [])),
            risk_level=RiskLevel(payload.get("risk_level", "low")),
            execution_mode=payload.get("execution_mode", "tool_chain"),
        )

    def plan(self, contract: TaskContract, catalogue: Catalogue, analysis: TaskAnalysis) -> Plan:
        payload = self._structured(
            system=_PLANNER_SYSTEM,
            user=json.dumps(
                {
                    "user_request": contract.user_request,
                    "current_time": contract.current_time,
                    "analysis": _analysis_payload(analysis),
                    "tools": catalogue.as_prompt_payload(),
                },
                indent=2,
            ),
            schema=PLAN_SCHEMA,
        )
        return _plan_from_payload(payload)

    def repair(
        self,
        contract: TaskContract,
        catalogue: Catalogue,
        analysis: TaskAnalysis,
        previous: Plan,
        request: RepairRequest,
    ) -> Plan:
        payload = self._structured(
            system=_REPAIR_SYSTEM,
            user=json.dumps(
                {
                    "user_request": contract.user_request,
                    "current_time": contract.current_time,
                    "analysis": _analysis_payload(analysis),
                    "rejected_plan": {
                        "decision": previous.decision.value,
                        "steps": [
                            {"step_id": s.step_id, "tool": s.tool, "arguments": s.arguments}
                            for s in previous.steps
                        ],
                    },
                    "violations": request.violations,
                    "reason": request.reason,
                    "forbidden_changes": list(request.forbidden_changes),
                    "tools": catalogue.as_prompt_payload(),
                },
                indent=2,
            ),
            schema=PLAN_SCHEMA,
        )
        return _plan_from_payload(payload)

    def judge(self, contract: TaskContract, transcript: list[dict[str, Any]]) -> tuple[bool, str]:
        if not self.config.enable_judge:
            return (True, "semantic judge disabled")
        payload = self._structured(
            system=(
                "You are the advisory semantic verifier. Decide only whether the executed "
                "trace satisfies the user's request as written. You are not authoritative: "
                "deterministic oracles decide pass/fail. Be strict about scope."
            ),
            user=json.dumps(
                {"user_request": contract.user_request, "trace": transcript}, indent=2
            ),
            schema={
                "type": "object",
                "properties": {
                    "satisfied": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["satisfied", "reason"],
                "additionalProperties": False,
            },
        )
        return (bool(payload["satisfied"]), str(payload["reason"]))

    # ---- transport --------------------------------------------------------

    def _structured(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.config.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }

        response = self._send(request)
        elapsed = (time.perf_counter() - started) * 1000

        self.usage.calls += 1
        self.usage.latency_ms += elapsed
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.usage.output_tokens += getattr(usage, "output_tokens", 0) or 0

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise BackendUnavailable(
                f"the request was declined by safety classifiers (category={category!r}). "
                "This is a content outcome, not a transport error."
            )

        for block in response.content:
            if getattr(block, "type", None) == "text":
                parsed: dict[str, Any] = json.loads(block.text)
                return parsed
        raise BackendUnavailable("model returned no text block to parse")

    def _send(self, request: dict[str, Any]) -> Any:
        if self.config.server_side_fallback:
            try:
                return self._client.beta.messages.create(
                    **request,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except Exception:  # noqa: BLE001 - beta surface may be unavailable
                # Fall through to the stable endpoint rather than failing the run.
                pass
        return self._client.messages.create(**request)


def _analysis_payload(analysis: TaskAnalysis) -> dict[str, Any]:
    return {
        "primary_intent": analysis.primary_intent,
        "target": analysis.target,
        "requested_effect": analysis.requested_effect,
        "requires_existing_message": analysis.requires_existing_message,
        "dependencies": analysis.dependencies,
        "ambiguities": analysis.ambiguities,
        "risk_level": analysis.risk_level.value,
        "execution_mode": analysis.execution_mode,
    }


def _plan_from_payload(payload: dict[str, Any]) -> Plan:
    return Plan(
        decision=Decision(payload.get("decision", "execute")),
        steps=[
            PlanStep(
                step_id=str(step["step_id"]),
                tool=str(step["tool"]),
                arguments=dict(step.get("arguments", {})),
            )
            for step in payload.get("steps", [])
        ],
        clarification_question=payload.get("clarification_question"),
        rationale=str(payload.get("rationale", "")),
    )
