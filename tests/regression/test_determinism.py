"""Regression guards on the properties a published result depends on.

If any of these break, previously reported numbers are no longer reproducible,
which is a bigger event than a failing feature test. They are deliberately
strict.
"""

from __future__ import annotations

import json

import pytest

from callbench.datasets.generate import GeneratorConfig, generate_partition
from callbench.datasets.task import Task
from callbench.simulator import build_fixture

CONFIG = GeneratorConfig(size=8, seed=20260805)
PARTITIONS = ("public", "validation", "hidden", "adversarial", "stress")


@pytest.mark.regression
@pytest.mark.parametrize(
    "fixture_id",
    ["fixture_std_201", "fixture_std_400", "fixture_inj_807", "fixture_str_1000"],
)
def test_fixture_hash_is_stable_across_builds(fixture_id: str) -> None:
    hashes = {build_fixture(fixture_id).state_hash() for _ in range(3)}
    assert len(hashes) == 1


@pytest.mark.regression
@pytest.mark.parametrize("partition", PARTITIONS)
def test_generation_is_reproducible(partition: str) -> None:
    first = generate_partition(partition, CONFIG)
    second = generate_partition(partition, CONFIG)
    assert [t.to_dict() for t in first] == [t.to_dict() for t in second]


@pytest.mark.regression
@pytest.mark.parametrize("partition", PARTITIONS)
def test_tasks_round_trip_through_json(partition: str) -> None:
    for task in generate_partition(partition, CONFIG):
        restored = Task.from_dict(json.loads(json.dumps(task.to_dict())))
        assert restored.to_dict() == task.to_dict()


@pytest.mark.regression
@pytest.mark.parametrize("partition", PARTITIONS)
def test_task_ids_are_unique(partition: str) -> None:
    tasks = generate_partition(partition, CONFIG)
    assert len({t.id for t in tasks}) == len(tasks)


@pytest.mark.regression
@pytest.mark.parametrize("partition", PARTITIONS)
def test_every_task_has_a_usable_oracle(partition: str) -> None:
    for task in generate_partition(partition, CONFIG):
        oracle = task.oracle
        assert oracle.decision in {"execute", "clarify", "refuse"}
        assert oracle.predicates, f"{task.id} has no state predicate to verify against"
        if oracle.decision == "execute" and oracle.expected_changed_resources:
            assert oracle.required_tools, f"{task.id} expects a change but requires no tool"


@pytest.mark.regression
def test_a_seed_change_changes_the_suite() -> None:
    """Otherwise the seed is decorative and the hidden partition is not held out."""
    a = generate_partition("public", CONFIG)
    b = generate_partition("public", GeneratorConfig(size=8, seed=1))
    assert [t.to_dict() for t in a] != [t.to_dict() for t in b]


@pytest.mark.regression
def test_hidden_partition_is_paraphrased_and_renamed() -> None:
    hidden = generate_partition("hidden", CONFIG)
    public = {t.prompt for t in generate_partition("public", CONFIG)}
    assert all(t.catalogue == "catalogue_v4" for t in hidden)
    assert all("renamed_catalogue" in t.difficulty_factors for t in hidden)
    assert not (public & {t.prompt for t in hidden}), "hidden prompts must not be verbatim copies"


@pytest.mark.regression
def test_splits_are_disjoint_at_the_fixture_level() -> None:
    """Held out has to mean held out.

    Salting the PRNG alone is not enough: templated prompts collide as strings
    across splits. Disjoint fixture ranges are what make a validation task a
    genuinely different question from a public one.
    """
    import itertools

    fixtures = {
        split: {t.fixture for t in generate_partition(split, GeneratorConfig(size=40, seed=1))}
        for split in PARTITIONS
    }
    for a, b in itertools.combinations(fixtures, 2):
        assert not (fixtures[a] & fixtures[b]), f"{a} and {b} share fixtures"


@pytest.mark.regression
def test_reproducibility_fingerprint_is_stable_and_sensitive() -> None:
    from callbench import repro
    from callbench.orchestration.config import BASELINES

    systems = list(BASELINES)
    first = repro.fingerprint(model="reference", systems=systems, partitions=["public"])
    second = repro.fingerprint(model="reference", systems=systems, partitions=["public"])
    assert first.replay_id == second.replay_id

    other = repro.fingerprint(model="claude-opus-5", systems=systems, partitions=["public"])
    assert other.replay_id != first.replay_id
    assert "planner" in first.diff(other)


@pytest.mark.regression
def test_behavioural_stability_against_the_committed_baseline() -> None:
    """Behavioural Replay Verification, as a check rather than a claim.

    The baseline is committed, so a change to the simulator's *observable*
    behaviour fails here rather than silently renumbering everyone's prior
    results. A pure refactor leaves this green.
    """
    from callbench import stability

    report = stability.measure()
    assert report is not None, "the baseline must be committed"
    assert report.stable, f"behavioural drift in {report.drifted}"
    assert report.score == 100.0


@pytest.mark.regression
def test_behavioural_stability_detects_a_real_change() -> None:
    """A check that cannot fail is not a check."""
    import callbench.simulator.tools as tools_module
    from callbench import stability

    baseline = stability.record()
    original = tools_module.HANDLERS["archive_message"]

    def altered(store, args, now):  # type: ignore[no-untyped-def]
        result = original(store, args, now)
        store.labels.add("DRIFT_PROBE")
        return result

    tools_module.HANDLERS["archive_message"] = altered
    try:
        drifted = stability.compare(baseline, stability.record())
    finally:
        tools_module.HANDLERS["archive_message"] = original

    assert not drifted.stable
    assert drifted.score == 0.0


@pytest.mark.regression
def test_the_state_model_covers_every_mutable_surface() -> None:
    """A reachable mutation that no snapshot records is invisible to every
    verifier downstream."""
    from callbench.simulator import build_fixture

    store = build_fixture("fixture_std_201")
    paths = set(store.snapshot())
    assert any(p.startswith("mailbox/labels") for p in paths)
    assert any(p.startswith("message/") for p in paths)
    assert any(p.startswith("thread/") for p in paths)
