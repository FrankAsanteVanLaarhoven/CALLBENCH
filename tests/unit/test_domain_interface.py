"""The domain interface, held honest by a second domain.

"Domain independent" is a claim about the core. The only way to keep it true is
to drive a domain that is not email through the same machinery, so this module
registers a throwaway one and exercises the interface against it.
"""

from __future__ import annotations

from typing import Any

import pytest

from callbench import domain as domain_module
from callbench.domain import BenchmarkDomain, EmailDomain, ReplayStep, get_domain

# ---- the shipped domain ---------------------------------------------------


def test_email_satisfies_the_interface() -> None:
    assert isinstance(EmailDomain(), BenchmarkDomain)


def test_the_registry_resolves_the_default_domain() -> None:
    assert get_domain().name == "email"
    assert get_domain("email") is domain_module.DOMAINS["email"]


def test_an_unknown_domain_names_what_is_available() -> None:
    with pytest.raises(KeyError, match="unknown domain"):
        get_domain("kubernetes")


def test_the_adapter_does_not_fork_the_implementation() -> None:
    """Extracting an interface must not create a second code path."""
    from callbench.simulator import build_fixture

    email = EmailDomain()
    assert email.state_hash(email.build_fixture("fixture_std_201")) == build_fixture(
        "fixture_std_201"
    ).state_hash()


def test_email_declares_every_piece_the_framework_consumes() -> None:
    email = EmailDomain()
    assert set(email.catalogues()) >= {"catalogue_v1", "catalogue_v4"}
    assert len(email.splits()) == 5
    assert len(email.tiers()) == 5
    assert email.predicates()
    assert email.mutations()
    assert email.replay_fixtures()
    assert all(isinstance(step, ReplayStep) for step in email.replay_script())


def test_the_replay_script_covers_every_side_effect_class() -> None:
    """A transition never exercised is one the stability check cannot protect."""
    from callbench.contracts import SideEffect

    email = EmailDomain()
    catalogue = email.catalogues()["catalogue_v1"]
    exercised = {catalogue.spec(step.tool).side_effect for step in email.replay_script()}
    assert exercised == set(SideEffect)


# ---- a second domain ------------------------------------------------------


class CounterDomain:
    """A deliberately trivial non-email domain.

    Its only purpose is to prove the interface admits something that is not a
    mailbox. It is a test double, not a benchmark: it ships no oracles and is
    never registered outside this module.
    """

    name = "counter"

    def build_fixture(self, fixture_id: str) -> dict[str, int]:
        return {"value": len(fixture_id) % 7}

    def invoke(self, state: dict[str, int], tool: str, arguments: dict[str, Any], now: str):
        if tool == "increment":
            state["value"] += int(arguments.get("by", 1))
            return {"value": state["value"]}
        if tool == "read":
            return {"value": state["value"]}
        raise KeyError(tool)

    def snapshot(self, state: dict[str, int]) -> dict[str, str]:
        return {"counter/value": str(state["value"])}

    def state_hash(self, state: dict[str, int]) -> str:
        return f"counter:{state['value']}"

    def catalogues(self):  # type: ignore[no-untyped-def]
        return {}

    def splits(self) -> tuple[str, ...]:
        return ("public",)

    def tiers(self) -> tuple[str, ...]:
        return ("easy",)

    def generate(self, split: str, size: int, seed: int):  # type: ignore[no-untyped-def]
        return []

    def predicates(self):  # type: ignore[no-untyped-def]
        return {}

    def mutations(self) -> tuple[Any, ...]:
        return ()

    def replay_script(self) -> tuple[ReplayStep, ...]:
        return (ReplayStep("read", "none"), ReplayStep("increment", "by_one"))

    def replay_fixtures(self) -> tuple[str, ...]:
        return ("counter_a", "counter_bb")


def test_a_non_email_domain_satisfies_the_interface() -> None:
    assert isinstance(CounterDomain(), BenchmarkDomain)


def test_a_second_domain_can_be_registered_and_resolved() -> None:
    counter = CounterDomain()
    domain_module.register(counter)
    try:
        assert get_domain("counter") is counter
    finally:
        domain_module.DOMAINS.pop("counter", None)


def test_registration_refuses_to_shadow_an_existing_domain() -> None:
    """Two task sets answering to one name would make every result ambiguous."""
    with pytest.raises(KeyError, match="already registered"):
        domain_module.register(EmailDomain())


def test_behavioural_equivalence_is_computable_for_a_non_email_domain() -> None:
    """Behavioural Replay Verification is stated over state transitions, and
    nothing in it is about email — so it must compute here too."""
    counter = CounterDomain()

    def signature(domain: Any) -> dict[str, str]:
        signatures: dict[str, str] = {}
        for fixture in domain.replay_fixtures():
            state = domain.build_fixture(fixture)
            transitions: list[str] = []
            for step in domain.replay_script():
                before = domain.state_hash(state)
                domain.invoke(state, step.tool, {"by": 1}, "now")
                transitions.append(f"{before}->{domain.state_hash(state)}")
            signatures[fixture] = "|".join(transitions)
        return signatures

    from callbench.stability import compare

    first, second = signature(counter), signature(counter)
    assert compare(first, second).stable

    drifted = dict(second)
    drifted["counter_a"] = "tampered"
    assert not compare(first, drifted).stable
