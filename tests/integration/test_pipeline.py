"""End-to-end pipeline behaviour.

These are the tests that would catch a harness which reports plausible numbers
for the wrong reasons: an oracle that no correct agent can satisfy, a gate that
never fires, or an ablation that changes nothing.
"""

from __future__ import annotations

import pytest

from callbench.models.reference import Profile, ReferenceBackend, ReferenceConfig
from callbench.orchestration import Pipeline
from callbench.orchestration.config import BY_NAME, FULL

PARTITIONS = ("public", "validation", "hidden", "adversarial", "stress")


def _pipeline(system_name: str = "callbench_full") -> Pipeline:
    system = BY_NAME[system_name]
    backend = ReferenceBackend(
        ReferenceConfig(profile=Profile(system.reference_profile), strict_json=system.strict_json)
    )
    return Pipeline(backend, system)


@pytest.mark.parametrize("partition", PARTITIONS)
def test_full_system_solves_the_partition(suite, partition: str) -> None:
    """The reference planner under the full architecture must clear its own suite.

    This is a harness assertion, not a capability claim: if the reference
    planner cannot satisfy an oracle, the oracle is unsatisfiable and every
    model measured against it would be scored unfairly.
    """
    pipeline = _pipeline()
    results = [pipeline.run(task) for task in suite[partition]]
    failures = [r.task_id for r in results if not r.passed]
    assert not failures, f"unsatisfiable oracles in {partition}: {failures}"


@pytest.mark.parametrize("partition", PARTITIONS)
def test_full_system_takes_no_unsafe_action(suite, partition: str) -> None:
    pipeline = _pipeline()
    unsafe = [r.task_id for r in (pipeline.run(t) for t in suite[partition]) if r.unsafe]
    assert not unsafe


def test_guessing_baseline_fabricates_and_the_gate_catches_it(suite) -> None:
    pipeline = _pipeline("direct_tool_calling")
    results = [pipeline.run(task) for task in suite["public"]]
    assert any(r.fabrication_count > 0 for r in results), (
        "the guessing baseline must produce fabricated identifiers, otherwise "
        "the provenance layer is untested"
    )


def test_removing_the_gate_changes_the_outcome(suite) -> None:
    """An ablation that changes nothing is not an ablation."""
    with_gate = [_pipeline("callbench_full").run(t) for t in suite["adversarial"]]
    without_gate = [_pipeline("ablate_policy_guardian").run(t) for t in suite["adversarial"]]
    assert sum(r.passed for r in with_gate) > sum(r.passed for r in without_gate)


def test_removing_provenance_admits_fabrications(suite) -> None:
    """Provenance is the layer that turns a plausible id into a caught one.

    With it on, a guessing planner's invented identifier is reported as T05 and
    blocked before execution. With it off the same plan reaches the simulator,
    so the fabrication is no longer visible as a fabrication — it surfaces, if
    at all, as some downstream symptom.
    """
    backend = ReferenceBackend(ReferenceConfig(profile=Profile.GUESSING))
    task = next(t for t in suite["public"] if "dependency_chain" in t.difficulty_factors)

    guarded = Pipeline(backend, FULL).run(task)
    unguarded = Pipeline(backend, BY_NAME["ablate_provenance"]).run(task)

    assert "T05_FABRICATED_IDENTIFIER" in guarded.emitted_codes
    assert guarded.fabrication_count > 0
    # Not "fewer fabrications" — the same plan is emitted either way. Without
    # provenance the fabrication is simply no longer observable as one.
    assert "T05_FABRICATED_IDENTIFIER" not in unguarded.emitted_codes


def test_state_is_never_mutated_twice_by_a_repair(suite) -> None:
    """The retry policy's core guarantee: no second send, ever."""
    pipeline = _pipeline()
    for task in suite["public"]:
        result = pipeline.run(task)
        mutating_attempts = [
            attempt
            for attempt in result.attempts
            if any(record.changed_resources for record in attempt.execution)
        ]
        assert len(mutating_attempts) <= 1, f"{task.id} changed state on more than one attempt"


def test_every_case_is_replayable(suite) -> None:
    pipeline = _pipeline()
    task = suite["public"][0]
    first = pipeline.run(task)
    second = pipeline.run(task)
    assert first.error_codes == second.error_codes
    assert first.passed == second.passed


def test_case_result_serialises(suite) -> None:
    result = _pipeline().run(suite["public"][0])
    payload = result.to_json()
    assert payload["task_id"] == suite["public"][0].id
    assert isinstance(payload["attempts"], list)


def test_hidden_partition_uses_the_renamed_catalogue(suite) -> None:
    assert all(task.catalogue == "catalogue_v4" for task in suite["hidden"])
