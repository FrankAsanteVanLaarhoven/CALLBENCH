"""Reproducibility fingerprints and replay.

"Reproducible" is a claim, and a claim needs a check. Every run computes a
fingerprint over the things that decide its numbers — the tool schemas, the
taxonomy, the fixture generator, the verifier, the scoring weights, the system
configurations, the dataset seed — and reduces them to a **replay id**.

``callbench replay <run_id>`` recomputes those component hashes against the
current tree and reports, component by component, which ones moved. That turns
"I cannot reproduce your numbers" from an argument into a diff: if the schema
hash changed, the tool surface changed, and the old numbers describe a
different benchmark.

Deliberately excluded from the fingerprint: wall-clock time, hostname, and
absolute paths. Including them would make every run irreproducible by
definition, which is the failure mode this module exists to avoid.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import __version__


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class Fingerprint:
    """Component hashes plus the replay id derived from them."""

    replay_id: str
    components: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def diff(self, other: Fingerprint) -> dict[str, tuple[str, str]]:
        """Components that differ, as ``{name: (recorded, current)}``."""
        keys = set(self.components) | set(other.components)
        return {
            key: (self.components.get(key, "<absent>"), other.components.get(key, "<absent>"))
            for key in sorted(keys)
            if self.components.get(key) != other.components.get(key)
        }


def _schema_hash() -> str:
    from .schemas.tools import CATALOGUES

    return _digest(
        {
            name: [
                {"name": spec.name, "schema": spec.input_schema,
                 "side_effect": spec.side_effect.value}
                for spec in catalogue
            ]
            for name, catalogue in sorted(CATALOGUES.items())
        }
    )


def _taxonomy_hash() -> str:
    from .taxonomy import _CODES

    return _digest(
        [
            {"code": c.code, "family_code": c.family_code, "safety_critical": c.safety_critical}
            for c in _CODES
        ]
    )


def _simulator_hash() -> str:
    """Hash a fixed sample of fixtures, not the source.

    Hashing behaviour rather than code means a refactor that preserves every
    mailbox does not invalidate a prior run — and a "harmless" change that
    silently alters one does.
    """
    from .simulator import build_fixture

    samples = ["fixture_std_201", "fixture_inj_207", "fixture_str_1000", "fixture_std_400"]
    return _digest({name: build_fixture(name).state_hash() for name in samples})


def _verifier_hash() -> str:
    from .metrics.score import PENALTIES, WEIGHTS
    from .metrics.trust import WEIGHTS as TRUST_WEIGHTS
    from .verification.predicates import PREDICATES

    return _digest(
        {
            "predicates": sorted(PREDICATES),
            "score_weights": WEIGHTS,
            "penalties": PENALTIES,
            "trust_weights": TRUST_WEIGHTS,
        }
    )


def _systems_hash(systems: list[Any]) -> str:
    return _digest(
        [
            {
                "name": s.name,
                "use_analyst": s.use_analyst,
                "gate": asdict(s.gate),
                "max_repairs": s.max_repairs,
                "verify_state": s.verify_state,
                "reference_profile": s.reference_profile,
                "strict_json": s.strict_json,
            }
            for s in systems
        ]
    )


def fingerprint(
    *,
    model: str,
    systems: list[Any],
    partitions: list[str],
    dataset_root: Path | None = None,
    seed: int | None = None,
    effort: str = "high",
) -> Fingerprint:
    components = {
        "schemas": _schema_hash(),
        "taxonomy": _taxonomy_hash(),
        "simulator": _simulator_hash(),
        "verifier": _verifier_hash(),
        "systems": _systems_hash(systems),
        "planner": _digest({"model": model, "effort": effort}),
        "dataset": _dataset_hash(dataset_root, partitions),
    }
    environment = {
        "callbench": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    config = {
        "model": model,
        "effort": effort,
        "partitions": partitions,
        "seed": seed,
        "systems": [s.name for s in systems],
    }
    # The environment is recorded but not hashed into the replay id: a run on a
    # different Python patch release is still the same experiment, and pinning
    # it would mean no run is ever reproducible on another machine.
    replay_id = "rp_" + _digest({"components": components, "config": config})
    return Fingerprint(replay_id, components, environment, config)


def _dataset_hash(root: Path | None, partitions: list[str]) -> str:
    """Hash the task files actually read, not the generator.

    A dataset is only the same dataset if the bytes are the same. Hashing the
    generator would let a regenerated suite claim identity with one it differs
    from.
    """
    if root is None:
        return "not-read"
    payload: dict[str, str] = {}
    for partition in sorted(partitions):
        path = root / partition / "tasks.jsonl"
        if not path.exists():
            payload[partition] = "<missing>"
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        payload[partition] = digest.hexdigest()[:16]
    return _digest(payload)


def load(path: Path) -> Fingerprint | None:
    """Read the fingerprint recorded in a results file."""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = payload.get("reproducibility")
    if not recorded:
        return None
    return Fingerprint(
        replay_id=recorded["replay_id"],
        components=recorded.get("components", {}),
        environment=recorded.get("environment", {}),
        config=recorded.get("config", {}),
    )
