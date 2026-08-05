"""The frozen v1.0 benchmark specification.

A benchmark that changes underneath its results is not a reference point. The
specification freeze records, once, the things a published number depends on —
the tool catalogue, the failure taxonomy, the oracle predicate set, the metric
weights, the split structure and the dataset bytes — and `callbench spec`
checks the current tree against it.

The freeze is **not** a version number in a file. It is a manifest of hashes,
so "we are still running v1.0" is a checkable claim rather than an assertion.
Anything that moves a hash moves the specification, and the diff names it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SPEC = Path("docs/spec-v1.0.json")


@dataclass
class Spec:
    version: str
    components: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    frozen: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def diff(self, other: Spec) -> dict[str, tuple[str, str]]:
        keys = set(self.components) | set(other.components)
        return {
            key: (self.components.get(key, "<absent>"), other.components.get(key, "<absent>"))
            for key in sorted(keys)
            if self.components.get(key) != other.components.get(key)
        }


def current(*, version: str = "1.0", dataset_root: Path | None = None) -> Spec:
    from . import repro
    from .datasets.generate import PARTITIONS, TIERS
    from .mutations import MUTATIONS
    from .orchestration.config import BASELINES
    from .schemas.tools import CANONICAL_TOOLS
    from .taxonomy import ALL_CODES
    from .verification.predicates import PREDICATES

    fingerprint = repro.fingerprint(
        model="reference",
        systems=list(BASELINES),
        partitions=list(PARTITIONS),
        dataset_root=dataset_root,
    )
    return Spec(
        version=version,
        components=fingerprint.components,
        counts={
            "tools": len(CANONICAL_TOOLS),
            "taxonomy_codes": len(ALL_CODES),
            "oracle_predicates": len(PREDICATES),
            "mutation_operators": len(MUTATIONS),
            "splits": len(PARTITIONS),
            "tiers": len(TIERS),
        },
    )


def load(path: Path = DEFAULT_SPEC) -> Spec | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Spec(
        version=raw["version"],
        components=raw.get("components", {}),
        counts=raw.get("counts", {}),
        frozen=raw.get("frozen", ""),
    )


def freeze(
    path: Path = DEFAULT_SPEC,
    *,
    version: str = "1.0",
    dataset_root: Path | None = None,
    frozen_on: str = "",
) -> Spec:
    spec = current(version=version, dataset_root=dataset_root)
    spec.frozen = frozen_on
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "definition": (
                    "The frozen specification is a manifest of component hashes, not a "
                    "version string. 'We are still running v1.0' is therefore checkable: "
                    "anything that moves a hash moves the specification."
                ),
                **spec.to_dict(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return spec


def verify(
    path: Path = DEFAULT_SPEC, *, dataset_root: Path | None = None
) -> tuple[Spec | None, dict[str, tuple[str, str]]]:
    frozen = load(path)
    if frozen is None:
        return (None, {})
    return (frozen, frozen.diff(current(version=frozen.version, dataset_root=dataset_root)))
