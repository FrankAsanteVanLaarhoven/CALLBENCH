from __future__ import annotations

import pytest

from callbench.datasets.generate import GeneratorConfig, generate_partition
from callbench.schemas import get_catalogue
from callbench.simulator import build_fixture

SMALL = GeneratorConfig(size=12, seed=20260805)


@pytest.fixture
def store():
    return build_fixture("fixture_std_201")


@pytest.fixture
def injected_store():
    return build_fixture("fixture_inj_204")


@pytest.fixture
def catalogue():
    return get_catalogue("catalogue_v1")


@pytest.fixture(scope="session")
def suite() -> dict[str, list]:
    return {
        partition: generate_partition(partition, SMALL)
        for partition in ("easy", "medium", "hard", "adversarial", "hidden")
    }
