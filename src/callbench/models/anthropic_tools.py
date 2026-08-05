"""A second Claude backend, implemented independently of the first.

:mod:`callbench.models.anthropic_backend` asks for a plan as constrained JSON
(``output_config.format``). This one never uses constrained output at all: both
roles go through the **native tool-use API** with ``strict: true``. The analysis
arrives as a forced call to a ``record_analysis`` tool; the plan is harvested
from the sequence of tool calls the model actually makes.

Two independent adapters over the same contract is the point. If a benchmark
only ever admits one implementation strategy, its conformance suite is
untested and its cross-model numbers are hostage to a single code path.

Symbolic dry-run results
========================

Planning must not execute. But a model that calls ``search_messages`` and gets
nothing back cannot compose a follow-up call except by inventing an identifier
— which would make every plan a fabrication and tell us nothing about the
model.

So the loop returns **symbolic** results: ``search_messages`` at step ``s1``
returns ``{"results": [{"thread_id": "$s1.results[0].thread_id", ...}]}``. The
value *is* its own provenance reference. A model that uses what the tool gave
it produces a reference; a model that invents an id produces a literal. The
distinction the benchmark measures survives intact, and nothing is executed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from ..contracts import Decision, Plan, PlanStep, RiskLevel, TaskAnalysis, TaskContract
from ..schemas import Catalogue
from .anthropic_backend import BackendUnavailable
from .base import ANALYSIS_SCHEMA, RepairRequest, Usage

MAX_TOKENS = 16000
MAX_PLAN_STEPS = 8


@dataclass(frozen=True)
class ToolUseConfig:
    model: str = "claude-opus-5"
    effort: str = "high"
    server_side_fallback: bool = True
    max_steps: int = MAX_PLAN_STEPS


_PLANNER_SYSTEM = """\
You are the Tool Planner in a constrained email agent, operating in PLAN-ONLY
mode. Call the tools you would use, in order. Nothing you call is executed.

Tool results you receive are symbolic placeholders, not real data: a value like
"$s1.results[0].thread_id" is a reference to whatever step s1 will actually
return at execution time. Use those values exactly as given — passing a
reference through is correct and expected.

Hard rules:
- Never invent an identifier, address, attachment id or date. If you did not
  receive it, you do not have it.
- Use only the tools provided. Familiar names from other systems do not exist
  here.
- Resolve relative dates against current_time before calling.
- When you are done planning, stop calling tools and reply with one short
  sentence. If the request is under-determined in a way that would change which
  resource is affected, say so instead of planning.
"""

_ANALYST_SYSTEM = """\
You are the Contract Analyst. Call record_analysis exactly once with your typed
interpretation of the request. You have no other tools and you never see the
mailbox.

Report an ambiguity whenever executing under either reading would change a
different resource. Record exclusions the request states, by address when it
gives one and by description when it does not.
"""


class AnthropicToolUseBackend:
    """Claude via native tool use. No constrained output anywhere."""

    def __init__(self, config: ToolUseConfig | None = None) -> None:
        self.config = config or ToolUseConfig()
        self.name = f"{self.config.model}+tooluse"
        self.usage = Usage()
        self._client = self._connect()

    def _connect(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BackendUnavailable(
                "the anthropic SDK is not installed. Install it with "
                "`pip install 'callbench[anthropic]'`, or run offline with "
                "`--model reference`."
            ) from exc
        if os.environ.get("ANTHROPIC_API_KEY", "").strip() in {"", "ollama", "none", "unset"}:
            # A placeholder key shadows every credential source and produces a
            # 401 thousands of cases into a run. Fail here instead.
            raise BackendUnavailable(
                "ANTHROPIC_API_KEY is unset or holds a placeholder value. Unset it and "
                "run `ant auth login`, or export a real key."
            )
        return anthropic.Anthropic()

    # ---- roles ------------------------------------------------------------

    def analyse(self, contract: TaskContract, catalogue: Catalogue) -> TaskAnalysis:
        payload = self._forced_call(
            system=_ANALYST_SYSTEM,
            user=json.dumps(
                {
                    "user_request": contract.user_request,
                    "current_time": contract.current_time,
                    "available_tool_names": list(catalogue.names),
                },
                indent=2,
            ),
            tool={
                "name": "record_analysis",
                "description": "Record the typed interpretation of the user's request.",
                "input_schema": ANALYSIS_SCHEMA,
                "strict": True,
            },
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
        return self._plan_loop(contract, catalogue, analysis, extra="")

    def repair(
        self,
        contract: TaskContract,
        catalogue: Catalogue,
        analysis: TaskAnalysis,
        previous: Plan,
        request: RepairRequest,
    ) -> Plan:
        extra = (
            "\n\nA previous plan was rejected with these violations: "
            f"{request.violations}. Reason: {request.reason}.\n"
            "You may add discovery steps or correct payload shapes. You may NOT change "
            f"any of: {list(request.forbidden_changes)}. If the only fix requires one of "
            "those, stop calling tools and say you refuse."
        )
        return self._plan_loop(contract, catalogue, analysis, extra=extra)

    def judge(self, contract: TaskContract, transcript: list[dict[str, Any]]) -> tuple[bool, str]:
        return (True, "this backend does not perform semantic judgement")

    # ---- the tool-use planning loop ---------------------------------------

    def _plan_loop(
        self,
        contract: TaskContract,
        catalogue: Catalogue,
        analysis: TaskAnalysis,
        *,
        extra: str,
    ) -> Plan:
        tools = [
            {**spec, "strict": True} for spec in catalogue.as_prompt_payload()
        ]
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_request": contract.user_request,
                        "current_time": contract.current_time,
                        "analysis": {
                            "primary_intent": analysis.primary_intent,
                            "target": analysis.target,
                            "ambiguities": analysis.ambiguities,
                        },
                    },
                    indent=2,
                )
                + extra,
            }
        ]

        steps: list[PlanStep] = []
        rationale = ""

        for _ in range(self.config.max_steps):
            response = self._send(
                {
                    "model": self.config.model,
                    "max_tokens": MAX_TOKENS,
                    "system": _PLANNER_SYSTEM,
                    "messages": messages,
                    "tools": tools,
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": self.config.effort},
                }
            )
            self._account(response)

            if getattr(response, "stop_reason", None) == "refusal":
                return Plan(decision=Decision.REFUSE, rationale="declined by safety classifiers")

            calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            texts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
            if texts:
                rationale = " ".join(texts).strip()

            if not calls:
                break

            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for call in calls:
                step_id = f"s{len(steps) + 1}"
                steps.append(
                    PlanStep(step_id=step_id, tool=call.name, arguments=dict(call.input))
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(
                            _symbolic_result(catalogue.canonical(call.name), step_id)
                        ),
                    }
                )
            messages.append({"role": "user", "content": results})

        if not steps:
            return Plan(
                decision=Decision.CLARIFY,
                clarification_question=rationale or "Could you restate the request?",
                rationale=rationale or "no tool calls were attempted",
            )
        return Plan(decision=Decision.EXECUTE, steps=steps, rationale=rationale)

    # ---- transport --------------------------------------------------------

    def _forced_call(self, *, system: str, user: str, tool: dict[str, Any]) -> dict[str, Any]:
        response = self._send(
            {
                "model": self.config.model,
                "max_tokens": MAX_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": tool["name"]},
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": self.config.effort},
            }
        )
        self._account(response)
        if getattr(response, "stop_reason", None) == "refusal":
            raise BackendUnavailable("the request was declined by safety classifiers")
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise BackendUnavailable(f"model did not call {tool['name']!r}")

    def _send(self, request: dict[str, Any]) -> Any:
        started = time.perf_counter()
        try:
            if self.config.server_side_fallback:
                try:
                    return self._client.beta.messages.create(
                        **request,
                        betas=["server-side-fallback-2026-07-01"],
                        fallbacks="default",
                    )
                except Exception:  # noqa: BLE001 - beta surface may be unavailable
                    pass
            return self._client.messages.create(**request)
        finally:
            self.usage.latency_ms += (time.perf_counter() - started) * 1000

    def _account(self, response: Any) -> None:
        self.usage.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.usage.output_tokens += getattr(usage, "output_tokens", 0) or 0


def _symbolic_result(canonical_tool: str, step_id: str) -> dict[str, Any]:
    """A plausible result shape whose values are their own provenance references.

    Shapes mirror the simulator's real returns so the model sees a familiar
    structure; every identifier is the reference that would resolve to it at
    execution time. Free text is left as prose, because provenance does not
    govern bodies and subjects.
    """
    ref = f"${step_id}"
    if canonical_tool == "search_messages":
        return {
            "results": [
                {
                    "message_id": f"{ref}.results[0].message_id",
                    "thread_id": f"{ref}.results[0].thread_id",
                    "from": f"{ref}.results[0].from",
                    "subject": "(symbolic) matching message",
                    "received_at": f"{ref}.results[0].received_at",
                }
            ],
            "count": 1,
        }
    if canonical_tool == "read_thread":
        return {
            "thread_id": f"{ref}.thread_id",
            "subject": "(symbolic) thread",
            "participants": [f"{ref}.participants[0]"],
            "external_participants": [f"{ref}.external_participants[0]"],
            "messages": [{"message_id": f"{ref}.messages[0].message_id"}],
        }
    if canonical_tool == "read_message":
        return {
            "message_id": f"{ref}.message_id",
            "thread_id": f"{ref}.thread_id",
            "from": f"{ref}.from",
            "labels": ["(symbolic) label set"],
            "body": "(symbolic) message body",
        }
    if canonical_tool == "resolve_contact":
        return {
            "matches": [{"name": "(symbolic) contact", "email": f"{ref}.matches[0].email"}],
            "ambiguous": False,
        }
    if canonical_tool == "list_attachments":
        return {
            "message_id": f"{ref}.message_id",
            "attachments": [{"attachment_id": f"{ref}.attachments[0].attachment_id"}],
        }
    if canonical_tool == "list_labels":
        return {"labels": ["INBOX", "UNREAD", "IMPORTANT"]}
    if canonical_tool in {"create_draft", "update_draft"}:
        return {"draft_id": f"{ref}.draft_id"}
    # Write operations return an acknowledgement: a plan-only run must not
    # suggest that anything happened.
    return {
        "acknowledged": True,
        "note": "plan-only mode: this call was recorded, not executed",
    }
