"""The executor.

The executor receives an approved plan and nothing else. It cannot revise the
user's request, cannot re-plan, and cannot reach any surface other than the
local simulator. Its only judgement call is when to stop.

Every call follows the same fixed sequence: resolve references from prior
results, submit the resolved payload to the policy gate, execute only on
approval, then record the before/after hashes and the exact set of changed
resources. A vetoed call never runs — the veto is recorded instead.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from ..contracts import ExecutionRecord, Plan, Violation
from ..policies import Guardian, ProvenanceLedger
from ..policies.references import UnresolvedReference, resolve
from ..schemas import Catalogue
from ..simulator import invoke
from ..simulator.store import MailboxStore, ToolError


@dataclass
class ExecutionOutcome:
    records: list[ExecutionRecord] = field(default_factory=list)
    blocked: list[Violation] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    halted_at: str | None = None

    @property
    def blocked_codes(self) -> list[str]:
        return [v.code for v in self.blocked]


class Executor:
    """Runs an approved plan against the simulator."""

    def __init__(
        self,
        catalogue: Catalogue,
        store: MailboxStore,
        guardian: Guardian,
        *,
        current_time: str,
        simulation_only: bool = True,
    ) -> None:
        self.catalogue = catalogue
        self.store = store
        self.guardian = guardian
        self.current_time = current_time
        self.simulation_only = simulation_only and os.environ.get(
            "CALLBENCH_SIMULATION_ONLY", "1"
        ) != "0"

    def run(self, plan: Plan, ledger: ProvenanceLedger) -> ExecutionOutcome:
        outcome = ExecutionOutcome()

        for step in plan.steps:
            try:
                resolved = resolve(step.arguments, outcome.results)
            except UnresolvedReference as exc:
                outcome.blocked.append(
                    Violation("T10_MISSING_DEPENDENCY", str(exc), step.step_id)
                )
                outcome.halted_at = step.step_id
                break

            if not isinstance(resolved, dict):  # pragma: no cover - schema forbids
                outcome.blocked.append(
                    Violation("T03_INVALID_ARGUMENT_TYPE", "arguments are not an object", step.step_id)
                )
                outcome.halted_at = step.step_id
                break

            verdict = self.guardian.review_step(step, resolved, ledger, outcome.results)
            if not verdict.approved:
                outcome.blocked.extend(verdict.violations)
                outcome.halted_at = step.step_id
                break
            outcome.blocked.extend(v for v in verdict.violations if not v.fatal)

            record = self._invoke(step.step_id, step.tool, resolved)
            outcome.records.append(record)
            if not record.ok:
                outcome.halted_at = step.step_id
                break

            outcome.results[step.step_id] = record.result
            ledger.add_tool_output(step.step_id, record.result)

        return outcome

    def _invoke(self, step_id: str, tool: str, arguments: dict[str, Any]) -> ExecutionRecord:
        canonical = self.catalogue.canonical(tool)
        if self.simulation_only and canonical not in _SIMULATED_TOOLS:
            return ExecutionRecord(
                step_id=step_id,
                tool=tool,
                resolved_arguments=arguments,
                ok=False,
                error=(
                    f"{tool!r} is not backed by the simulator and simulation-only mode is "
                    "active; no real mail operation can be issued from this harness"
                ),
                before_hash=self.store.state_hash(),
                after_hash=self.store.state_hash(),
            )

        payload = self.catalogue.canonical_arguments(arguments)
        before_snapshot = self.store.snapshot()
        before_hash = self.store.state_hash()
        started = time.perf_counter()
        try:
            result = invoke(self.store, canonical, payload, self.current_time)
            ok, error = True, None
        except ToolError as exc:
            result, ok, error = None, False, str(exc)
        elapsed = (time.perf_counter() - started) * 1000

        after_snapshot = self.store.snapshot()
        return ExecutionRecord(
            step_id=step_id,
            tool=tool,
            resolved_arguments=arguments,
            ok=ok,
            result=result,
            error=error,
            before_hash=before_hash,
            after_hash=self.store.state_hash(),
            changed_resources=MailboxStore.diff(before_snapshot, after_snapshot),
            latency_ms=elapsed,
        )


from ..simulator.tools import HANDLERS as _HANDLERS  # noqa: E402  (cycle-free by construction)

_SIMULATED_TOOLS = frozenset(_HANDLERS)
