"""Agent roles.

The separation is structural, not stylistic. Each role is denied the capability
that would let it confirm its own conclusion:

===================  ================================================
Role                 Cannot
===================  ================================================
Contract Analyst     call tools, or see the mailbox
Tool Planner         execute anything
Policy Guardian      execute anything (it may only veto)
Executor             revise the request or re-plan
Verifier             modify state
Failure Analyst      retry a destructive action without renewed approval
===================  ================================================

The classes below enforce the first two and the last by construction: they are
handed only the objects their role permits.
"""

from __future__ import annotations

from ..contracts import Plan, TaskAnalysis, TaskContract
from ..models.base import Backend, RepairRequest
from ..schemas import Catalogue
from .executor import ExecutionOutcome, Executor

__all__ = ["Analyst", "ExecutionOutcome", "Executor", "FailureAnalyst", "Planner"]

#: Changes a repair may never make on its own initiative. Mirrors the retry
#: policy: a rejected send is not repaired by sending it somewhere else.
NEVER_RETRY = (
    "sending to a different recipient",
    "deleting a broader set of messages",
    "forwarding confidential material",
    "changing reply-all membership",
    "attaching a different file",
)


class Analyst:
    """Produces the typed interpretation. Holds no store and no executor."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def analyse(self, contract: TaskContract, catalogue: Catalogue) -> TaskAnalysis:
        return self._backend.analyse(contract, catalogue)


class Planner:
    """Chooses the minimum valid tool sequence. Holds no store."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def plan(
        self, contract: TaskContract, catalogue: Catalogue, analysis: TaskAnalysis
    ) -> Plan:
        return self._backend.plan(contract, catalogue, analysis)


class FailureAnalyst:
    """Classifies a failure and proposes a bounded repair."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def repair(
        self,
        contract: TaskContract,
        catalogue: Catalogue,
        analysis: TaskAnalysis,
        previous: Plan,
        *,
        reason: str,
        violations: list[str],
    ) -> Plan:
        return self._backend.repair(
            contract,
            catalogue,
            analysis,
            previous,
            RepairRequest(reason=reason, violations=violations),
        )
