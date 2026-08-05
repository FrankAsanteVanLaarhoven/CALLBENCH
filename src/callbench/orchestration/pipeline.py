"""The analyst -> planner -> guardian -> executor -> verifier pipeline.

The retry policy is the part worth reading twice. A repair is permitted only
when the failed attempt changed **nothing** in the mailbox. Once state has
moved, the trace is final: there is no second send, no second delete, and no
"try again with different recipients". That single rule is what makes bounded
repair safe to include in a benchmark that measures unsafe actions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..agents import Analyst, Executor, FailureAnalyst, Planner
from ..contracts import Attempt, CaseResult, Decision, Plan, RiskLevel, TaskAnalysis
from ..datasets.task import Task
from ..models.base import Backend
from ..policies import Guardian, ProvenanceLedger
from ..schemas import Catalogue, get_catalogue
from ..simulator import build_fixture
from ..verification import Verifier
from .config import SystemConfig


@dataclass
class PipelineDeps:
    backend: Backend
    system: SystemConfig


class Pipeline:
    """Runs one task end to end and produces a replayable :class:`CaseResult`."""

    def __init__(
        self,
        backend: Backend,
        system: SystemConfig,
        *,
        catalogue_override: Catalogue | None = None,
    ) -> None:
        self.backend = backend
        self.system = system
        #: Used by mutation testing to swap the tool surface without touching
        #: the task, the fixture or the oracle.
        self.catalogue_override = catalogue_override
        self.analyst = Analyst(backend)
        self.planner = Planner(backend)
        self.failure_analyst = FailureAnalyst(backend)
        #: Provenance ledger from the most recent run, so callers that want a
        #: graph do not have to re-execute the case to get one.
        self.last_ledger: ProvenanceLedger | None = None

    def run(self, task: Task) -> CaseResult:
        started = time.perf_counter()
        contract = task.contract()
        catalogue = self.catalogue_override or get_catalogue(task.catalogue)
        store = build_fixture(task.fixture)
        guardian = Guardian(catalogue, contract.policy, config=self.system.gate)
        executor = Executor(catalogue, store, guardian, current_time=contract.current_time)
        verifier = Verifier(catalogue)

        ledger = ProvenanceLedger()
        ledger.add_user_request(contract.user_request)
        self.last_ledger = ledger

        result = CaseResult(
            task_id=task.id,
            partition=task.partition,
            tier=task.tier,
            system=self.system.name,
            model=self.backend.name,
        )

        planning_started = time.perf_counter()
        analysis = (
            self.analyst.analyse(contract, catalogue)
            if self.system.use_analyst
            else _bare_analysis(contract.user_request)
        )
        planning_ms = (time.perf_counter() - planning_started) * 1000
        execution_ms = 0.0
        repair_ms = 0.0

        plan: Plan | None = None
        outcome = None
        blocked_codes: list[str] = []
        emitted_codes: list[str] = []

        for index in range(self.system.max_repairs + 1):
            attempt = Attempt(index=index, analysis=analysis)

            stage_started = time.perf_counter()
            if index == 0:
                plan = self.planner.plan(contract, catalogue, analysis)
                planning_ms += (time.perf_counter() - stage_started) * 1000
            else:
                previous = plan or Plan()
                plan = self.failure_analyst.repair(
                    contract,
                    catalogue,
                    analysis,
                    previous,
                    reason=attempt.repair_reason or "previous attempt was rejected",
                    violations=blocked_codes,
                )
                repair_ms += (time.perf_counter() - stage_started) * 1000
                if _same_plan(previous, plan):
                    # A repair that reproduces the rejected plan has nothing to
                    # add. Burning the remaining budget on identical attempts
                    # would inflate latency and retry rate without changing the
                    # outcome, so the attempt is abandoned here.
                    result.attempts[-1].repair_reason = (
                        "repair reproduced the rejected plan; retry budget abandoned"
                    )
                    break
            attempt.plan = plan

            guard = guardian.review_plan(plan, analysis)
            attempt.guard = guard

            if plan.decision is not Decision.EXECUTE:
                blocked_codes = []
                result.attempts.append(attempt)
                break

            emitted_codes.extend(v.code for v in guard.violations)
            if not guard.approved:
                blocked_codes = [v.code for v in guard.fatal_violations]
                attempt.repair_reason = "; ".join(v.message for v in guard.fatal_violations)
                result.attempts.append(attempt)
                if index == self.system.max_repairs:
                    break
                continue

            execution_started = time.perf_counter()
            outcome = executor.run(plan, ledger)
            execution_ms += (time.perf_counter() - execution_started) * 1000
            attempt.execution = outcome.records
            blocked_codes = outcome.blocked_codes
            emitted_codes.extend(blocked_codes)
            result.attempts.append(attempt)

            state_moved = any(record.changed_resources for record in outcome.records)
            if not blocked_codes and all(record.ok for record in outcome.records):
                break
            if state_moved:
                # Never repair past a committed side effect.
                attempt.repair_reason = (
                    "attempt changed mailbox state; repair suppressed by the retry policy"
                )
                break
            if index == self.system.max_repairs:
                break
            attempt.repair_reason = "; ".join(
                v.message for v in outcome.blocked
            ) or "execution failed before any state change"

        # A repair budget spent without resolving the failure is a legitimate
        # outcome — recorded as R01 rather than hidden, because "we tried twice
        # and stopped" is information a reviewer needs.
        if (
            self.system.max_repairs
            and len(result.attempts) > self.system.max_repairs
            and blocked_codes
        ):
            emitted_codes.append("T19_REPAIR_BUDGET_EXHAUSTED")

        final_plan = plan or Plan()
        records = outcome.records if outcome is not None else []
        advisory = None
        if self.system.advisory_judge:
            advisory = self.backend.judge(
                contract,
                [
                    {"tool": r.tool, "arguments": r.resolved_arguments, "ok": r.ok}
                    for r in records
                ],
            )

        verification_started = time.perf_counter()
        verdict, kpis = verifier.verify(
            oracle=task.oracle,
            plan=final_plan,
            records=records,
            final_store=store,
            blocked_codes=blocked_codes,
            emitted_codes=emitted_codes,
            advisory=advisory,
        )
        verification_ms = (time.perf_counter() - verification_started) * 1000

        if not self.system.verify_state:
            state_layer = verdict.layer("state_transition")
            if state_layer is not None:
                state_layer.authoritative = False
            verdict.passed = all(
                layer.passed for layer in verdict.layers if layer.authoritative
            )

        if result.attempts:
            result.attempts[-1].verdict = verdict

        result.passed = verdict.passed
        result.error_codes = verdict.error_codes
        result.emitted_codes = sorted(dict.fromkeys(emitted_codes))
        result.unsafe = verdict.unsafe
        result.fabrication_count = verdict.fabrication_count
        result.tool_calls = len(records)
        result.emitted_calls = int(kpis["emitted_calls"])
        result.schema_valid_calls = int(kpis["schema_valid_calls"])
        result.first_tool_correct = bool(kpis["first_tool_correct"])
        result.arguments_exact_match = bool(kpis["arguments_exact_match"])
        result.plan_success = bool(kpis["plan_success"])
        result.state_transition_ok = bool(kpis["state_transition_ok"])
        result.clarification_correct = kpis["clarification_correct"]
        result.planning_ms = planning_ms
        result.execution_ms = execution_ms
        result.verification_ms = verification_ms
        result.repair_ms = repair_ms
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.input_tokens = self.backend.usage.input_tokens
        result.output_tokens = self.backend.usage.output_tokens
        return result


def _same_plan(left: Plan, right: Plan) -> bool:
    if left.decision is not right.decision or len(left.steps) != len(right.steps):
        return False
    return all(
        a.tool == b.tool and a.arguments == b.arguments
        for a, b in zip(left.steps, right.steps, strict=True)
    )


def _bare_analysis(request: str) -> TaskAnalysis:
    """What the planner gets when the analyst role is ablated away.

    Deliberately impoverished: an intent guess and nothing else. No exclusions,
    no ambiguities, no dependency list — which is precisely the signal the
    analyst contributes and the ablation removes.
    """
    lowered = request.lower()
    intent = "unknown"
    for keyword in ("reply", "forward", "draft", "delete", "archive", "label", "send", "find"):
        if keyword in lowered:
            intent = keyword if keyword != "find" else "search"
            break
    return TaskAnalysis(
        primary_intent=intent,
        target={},
        requested_effect=request,
        requires_existing_message=intent not in {"send", "search", "unknown"},
        dependencies=[],
        ambiguities=[],
        risk_level=RiskLevel.MEDIUM,
        execution_mode="direct_call",
    )
