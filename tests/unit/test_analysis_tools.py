"""Decomposition, behavioural stability, dimensions and backend conformance."""

from __future__ import annotations

import pytest

from callbench import conformance, decompose, stability
from callbench.metrics import dimensions
from callbench.models.reference import ReferenceBackend

# ---- decomposition --------------------------------------------------------


def test_decomposition_fills_the_grid(suite) -> None:
    result = decompose.run(
        suite["public"][:6],
        decompose.reference_factory,
        planners=("guessing", "full"),
        architectures=("structured_outputs", "callbench_full"),
    )
    assert len(result.cells) == 4
    assert set(result.planners()) == {"guessing", "full"}


def test_decomposition_pins_the_planner_against_the_architecture(suite) -> None:
    """The two axes must move independently.

    Each architecture declares its own reference profile; if the factory did
    not override it, planner and architecture would move together and the
    decomposition would measure nothing.
    """
    from callbench.orchestration.config import BY_NAME

    backend = decompose.reference_factory("guessing", BY_NAME["callbench_full"])
    assert backend.name == "reference:guessing"
    assert BY_NAME["callbench_full"].reference_profile == "full"


def test_architecture_axis_has_no_duplicate_columns(suite) -> None:
    """Two architectures that behave identically make the axis look flatter
    than it is."""
    result = decompose.run(
        suite["public"][:8], decompose.reference_factory, planners=("shallow",)
    )
    rates = [c.pass_rate for c in result.cells]
    assert len(set(rates)) > 1, "the architecture ladder collapsed"


def test_attribution_is_reported_with_an_interaction(suite) -> None:
    result = decompose.run(
        suite["public"][:8],
        decompose.reference_factory,
        planners=("guessing", "full"),
        architectures=("structured_outputs", "callbench_full"),
    )
    attribution = result.attribution()
    assert set(attribution) == {
        "architecture_span", "planner_span", "architecture_share", "interaction"
    }
    assert 0.0 <= (attribution["architecture_share"] or 0.0) <= 1.0


# ---- behavioural stability ------------------------------------------------


def test_canonical_script_exercises_every_side_effect_class() -> None:
    from callbench.contracts import SideEffect
    from callbench.schemas import get_catalogue

    catalogue = get_catalogue("catalogue_v1")
    exercised = {catalogue.spec(step.tool).side_effect for step in stability.CANONICAL_SCRIPT}
    assert exercised == set(SideEffect), "an unexercised transition is unprotected"


def test_signatures_are_stable_across_repeated_traces() -> None:
    fixture = stability.CANONICAL_FIXTURES[0]
    assert stability.trace(fixture).signature == stability.trace(fixture).signature


def test_distinct_fixtures_have_distinct_signatures() -> None:
    signatures = stability.record()
    assert len(set(signatures.values())) == len(signatures)


def test_comparison_reports_added_and_missing_fixtures() -> None:
    report = stability.compare({"a": "x", "b": "y"}, {"a": "x", "c": "z"})
    assert report.drifted == []
    assert report.missing == ["c"]
    assert report.added == ["b"]
    assert not report.stable


# ---- dimensions -----------------------------------------------------------


def test_dimensions_cover_all_eight(suite) -> None:
    rows = dimensions.build([])
    assert len(rows) == 8
    assert {r.dimension.key for r in rows} == {
        "correctness", "safety", "reliability", "robustness",
        "efficiency", "cost", "reproducibility", "provenance",
    }


def test_unmeasured_dimensions_report_none_not_zero() -> None:
    """A zero reads as a measurement; an unmeasured dimension is not a finding."""
    rows = {r.dimension.key: r for r in dimensions.build([])}
    assert rows["reliability"].run_level is None
    assert rows["robustness"].values == {}


def test_run_level_dimensions_are_not_per_system() -> None:
    rows = {r.dimension.key: r for r in dimensions.build([], behavioural_stability=100.0)}
    assert rows["reliability"].dimension.scope == "run"
    assert rows["reliability"].run_level == 100.0
    assert rows["correctness"].dimension.scope == "system"


# ---- conformance ----------------------------------------------------------


def test_the_reference_backend_conforms() -> None:
    report = conformance.check(ReferenceBackend)
    failures = [c.name for c in report.checks if c.required and not c.passed]
    assert report.conformant, f"failed: {failures}"


def test_conformance_rejects_a_backend_that_invents_tools() -> None:
    """The suite has to be able to fail, or it certifies nothing."""
    from callbench.contracts import Decision, Plan, PlanStep

    class Rogue(ReferenceBackend):
        def plan(self, contract, catalogue, analysis):  # type: ignore[no-untyped-def]
            return Plan(
                decision=Decision.EXECUTE,
                steps=[PlanStep("s1", "send_email", {"to": ["x@y.test"]})],
            )

    report = conformance.check(Rogue)
    assert not report.conformant
    assert any(
        c.name == "every planned tool is in the supplied catalogue" and not c.passed
        for c in report.checks
    )


def test_conformance_rejects_a_repair_that_reaims_a_send() -> None:
    from callbench.contracts import Decision, Plan, PlanStep

    class Reaimer(ReferenceBackend):
        def repair(self, contract, catalogue, analysis, previous, request):  # type: ignore[no-untyped-def]
            return Plan(
                decision=Decision.EXECUTE,
                steps=[
                    PlanStep(
                        "s1",
                        "send_message",
                        {
                            "to": ["someone.else@company.test"],
                            "subject": "Contract",
                            "body": "Approved.",
                        },
                    )
                ],
            )

    report = conformance.check(Reaimer)
    assert not report.conformant
    assert any(
        c.name == "repair does not re-aim the recipient set" and not c.passed
        for c in report.checks
    )


def test_conformance_rejects_a_backend_that_drops_exclusions() -> None:
    from callbench.contracts import TaskAnalysis

    class Careless(ReferenceBackend):
        def analyse(self, contract, catalogue):  # type: ignore[no-untyped-def]
            analysis: TaskAnalysis = super().analyse(contract, catalogue)
            analysis.target.pop("exclude_recipients", None)
            analysis.target.pop("exclude_description", None)
            return analysis

    report = conformance.check(Careless)
    assert not report.conformant


@pytest.mark.parametrize("check_name", [
    "analyse returns a TaskAnalysis",
    "plan returns a Plan",
    "every planned tool is in the supplied catalogue",
    "no fabricated identifiers in the first plan",
    "an exclusion reaches the analysis",
    "repair does not re-aim the recipient set",
    "judge returns (bool, str)",
    "usage accounting is populated",
])
def test_contract_covers_the_named_check(check_name: str) -> None:
    report = conformance.check(ReferenceBackend)
    assert any(c.name == check_name for c in report.checks)


def test_retention_and_absolute_robustness_are_different_questions(suite) -> None:
    """A weak system can post high retention while performing terribly.

    Retention is a ratio to the system's own baseline, so a planner that barely
    consults the catalogue loses little when the catalogue changes. Ranking on
    retention would call that the most robust system, which is backwards — so
    the comparison table uses the absolute figure.
    """
    from callbench import mutations
    from callbench.orchestration.config import BY_NAME

    tasks = suite["public"][:8]
    weak = mutations.run(
        tasks, ReferenceBackend, BY_NAME["direct_tool_calling"],
        mutations=(mutations.BY_NAME["rename_tools"],),
    )
    strong = mutations.run(
        tasks, ReferenceBackend, BY_NAME["callbench_full"],
        mutations=(mutations.BY_NAME["rename_tools"],),
    )
    assert strong.absolute_score > weak.absolute_score
