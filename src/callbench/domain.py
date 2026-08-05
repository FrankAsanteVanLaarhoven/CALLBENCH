"""The benchmark domain interface.

CallBench is a framework with a first domain, not an email benchmark. The
distinction only means something if it is expressed as an interface, so this
module states exactly what a domain must supply and what the core promises not
to assume.

A domain owns four things and nothing else:

===================  ===================================================
Simulator            state, transitions, and a content-addressed snapshot
Catalogues           tool surfaces, including any renamed variant
Task generation      tasks with oracles computed from its own fixtures
Domain specifics     state predicates, mutation operators, replay script
===================  ===================================================

Everything else — the analyst/planner/guardian/executor/verifier pipeline, the
provenance ledger, the four verification layers, the taxonomy, the metrics, the
graphs, behavioural replay, certification, reproducibility — is domain
independent by construction and consumes only this interface.

That is the claim, and :mod:`tests` holds it honest: a throwaway second domain
is registered there and driven through the same machinery, so "domain
independent" is a passing test rather than an aspiration.

Physical layout
===============

The package keeps a flat layout (``simulator/``, ``schemas/``, ``datasets/``)
rather than the ``framework/`` + ``domains/`` tree a multi-domain repository
would eventually want. That migration is deliberately *not* done here: it would
move every module immediately after v1.0's specification hashes were frozen,
invalidating the freeze and the artifact for no functional gain. The interface
is what makes the migration mechanical when a second real domain arrives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .datasets.task import Task
from .schemas.tools import Catalogue


@dataclass(frozen=True)
class ReplayStep:
    """One step of a domain's canonical replay script."""

    tool: str
    resolve: str
    note: str = ""


@runtime_checkable
class BenchmarkDomain(Protocol):
    """What a domain must supply for the framework to evaluate it."""

    name: str

    # ---- simulator -------------------------------------------------------

    def build_fixture(self, fixture_id: str) -> Any:
        """Return the domain's state object for ``fixture_id``, deterministically."""
        ...

    def invoke(self, state: Any, tool: str, arguments: dict[str, Any], now: str) -> dict[str, Any]:
        """Execute one canonical tool against the state."""
        ...

    def snapshot(self, state: Any) -> dict[str, str]:
        """Resource path -> canonical serialisation, covering every mutable surface."""
        ...

    def state_hash(self, state: Any) -> str: ...

    # ---- tool surface ----------------------------------------------------

    def catalogues(self) -> dict[str, Catalogue]:
        """Named tool surfaces. At least one; a renamed variant enables the
        contamination control."""
        ...

    # ---- tasks -----------------------------------------------------------

    def splits(self) -> tuple[str, ...]: ...

    def tiers(self) -> tuple[str, ...]: ...

    def generate(self, split: str, size: int, seed: int) -> list[Task]:
        """Generate a split, with every oracle computed from the fixture built."""
        ...

    # ---- domain-specific evaluation --------------------------------------

    def predicates(self) -> dict[str, Callable[..., tuple[bool, str, str | None]]]:
        """Final-state predicates this domain's oracles may reference."""
        ...

    def mutations(self) -> tuple[Any, ...]:
        """Catalogue mutation operators meaningful for this tool surface."""
        ...

    def replay_script(self) -> tuple[ReplayStep, ...]:
        """Canonical operation script for behavioural replay verification.

        Must exercise every side-effect class the domain declares: a transition
        that is never exercised is a transition the stability check cannot
        protect.
        """
        ...

    def replay_fixtures(self) -> tuple[str, ...]: ...


class EmailDomain:
    """The first domain: email over a simulated mailbox.

    A thin adapter. Every method delegates to the existing modules, which is
    the point — extracting the interface must not fork the implementation, or
    the interface would describe something the benchmark does not run.
    """

    name = "email"

    # ---- simulator -------------------------------------------------------

    def build_fixture(self, fixture_id: str) -> Any:
        from .simulator import build_fixture

        return build_fixture(fixture_id)

    def invoke(self, state: Any, tool: str, arguments: dict[str, Any], now: str) -> dict[str, Any]:
        from .simulator import invoke

        return invoke(state, tool, arguments, now)

    def snapshot(self, state: Any) -> dict[str, str]:
        return dict(state.snapshot())

    def state_hash(self, state: Any) -> str:
        return str(state.state_hash())

    # ---- tool surface ----------------------------------------------------

    def catalogues(self) -> dict[str, Catalogue]:
        from .schemas.tools import CATALOGUES

        return dict(CATALOGUES)

    # ---- tasks -----------------------------------------------------------

    def splits(self) -> tuple[str, ...]:
        from .datasets.generate import PARTITIONS

        return PARTITIONS

    def tiers(self) -> tuple[str, ...]:
        from .datasets.generate import TIERS

        return TIERS

    def generate(self, split: str, size: int, seed: int) -> list[Task]:
        from .datasets.generate import GeneratorConfig, generate_partition

        return generate_partition(split, GeneratorConfig(size=size, seed=seed))

    # ---- domain-specific evaluation --------------------------------------

    def predicates(self) -> dict[str, Callable[..., tuple[bool, str, str | None]]]:
        from .verification.predicates import PREDICATES

        return dict(PREDICATES)

    def mutations(self) -> tuple[Any, ...]:
        from .mutations import MUTATIONS

        return MUTATIONS

    def replay_script(self) -> tuple[ReplayStep, ...]:
        from .stability import CANONICAL_SCRIPT

        return tuple(ReplayStep(s.tool, s.resolve, s.note) for s in CANONICAL_SCRIPT)

    def replay_fixtures(self) -> tuple[str, ...]:
        from .stability import CANONICAL_FIXTURES

        return CANONICAL_FIXTURES


DOMAINS: dict[str, BenchmarkDomain] = {"email": EmailDomain()}


def register(domain: BenchmarkDomain) -> None:
    """Register a domain. Refuses to shadow an existing name.

    Silently replacing a domain would let two different task sets answer to one
    name, and every result recorded against it would become ambiguous.
    """
    if domain.name in DOMAINS:
        raise KeyError(f"domain {domain.name!r} is already registered")
    DOMAINS[domain.name] = domain


def get_domain(name: str = "email") -> BenchmarkDomain:
    try:
        return DOMAINS[name]
    except KeyError as exc:
        raise KeyError(f"unknown domain {name!r}; have {sorted(DOMAINS)}") from exc
