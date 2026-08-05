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
PARTITIONS = ("easy", "medium", "hard", "adversarial", "hidden")


@pytest.mark.regression
@pytest.mark.parametrize("fixture_id", ["fixture_std_201", "fixture_std_242", "fixture_inj_207"])
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
    a = generate_partition("easy", CONFIG)
    b = generate_partition("easy", GeneratorConfig(size=8, seed=1))
    assert [t.to_dict() for t in a] != [t.to_dict() for t in b]


@pytest.mark.regression
def test_hidden_partition_is_paraphrased_and_renamed() -> None:
    hidden = generate_partition("hidden", CONFIG)
    easy = {t.prompt for t in generate_partition("easy", CONFIG)}
    assert all(t.catalogue == "catalogue_v4" for t in hidden)
    assert all("renamed_catalogue" in t.difficulty_factors for t in hidden)
    assert not (easy & {t.prompt for t in hidden}), "hidden prompts must not be verbatim copies"
