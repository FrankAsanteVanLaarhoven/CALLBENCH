"""Certification: the gate between a working adapter and an admissible result.

Conformance answers *is this adapter faithful?* Certification answers *may its
numbers appear in a comparison?* They are different questions, and collapsing
them is how invalid comparisons get published.

::

    Backend  ──▶  Conformance  ──▶  Certification  ──▶  Benchmark-eligible
                  (contract)        (recorded, dated)     (admissible)

A backend that fails conformance is **excluded**, and the exclusion is
reported rather than silently omitted — an absent row invites the reader to
assume the model was not tried, when in fact its adapter was not trustworthy.

Certification records four things: the backend identity, the conformance
result, the fingerprint of the tree it was certified against, and the date.
The third matters most: a certificate issued against a different schema
registry or taxonomy is not evidence about *this* benchmark, so certificates
carry the component hashes they were earned under and expire when those move.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import conformance
from .models.base import Backend

DEFAULT_REGISTRY = Path("docs/certified-backends.json")


class Status:
    CERTIFIED = "certified"
    EXCLUDED = "excluded"
    UNTESTED = "untested"
    STALE = "stale"


@dataclass
class Certificate:
    backend: str
    status: str
    issued: str
    #: Component hashes of the tree this certificate was earned against.
    components: dict[str, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def admissible(self) -> bool:
        return self.status == Status.CERTIFIED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Certificate:
        return Certificate(
            backend=raw["backend"],
            status=raw["status"],
            issued=raw.get("issued", ""),
            components=raw.get("components", {}),
            failures=list(raw.get("failures", [])),
            note=raw.get("note", ""),
        )


def issue(
    backend_factory: Callable[[], Backend],
    *,
    components: dict[str, str] | None = None,
    today: str | None = None,
) -> Certificate:
    """Run conformance and mint a certificate — or an exclusion."""
    try:
        report = conformance.check(backend_factory)
    except Exception as exc:  # noqa: BLE001 - a backend that cannot run is excluded
        return Certificate(
            backend="unknown",
            status=Status.EXCLUDED,
            issued=today or date.today().isoformat(),
            components=components or {},
            failures=[f"backend could not be constructed or exercised: {exc!r}"],
            note="excluded from comparison",
        )

    failures = [c.name for c in report.checks if c.required and not c.passed]
    return Certificate(
        backend=report.backend,
        status=Status.CERTIFIED if report.conformant else Status.EXCLUDED,
        issued=today or date.today().isoformat(),
        components=components or {},
        failures=failures,
        note=(
            "admissible for comparison"
            if report.conformant
            else "excluded: a comparison using this adapter would measure the adapter"
        ),
    )


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Certificate]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: Certificate.from_dict(raw)
        for name, raw in payload.get("certificates", {}).items()
    }


def write_registry(
    certificates: dict[str, Certificate], path: Path = DEFAULT_REGISTRY
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "definition": (
                    "A backend is benchmark-eligible only when it holds a current "
                    "certificate: it passed the adapter conformance contract against a "
                    "tree whose component hashes match the run's. Excluded backends are "
                    "listed rather than omitted, because an absent row reads as 'not "
                    "tried' when it means 'not trustworthy'."
                ),
                "certificates": {k: v.to_dict() for k, v in sorted(certificates.items())},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def status_for(
    backend_name: str,
    components: dict[str, str],
    *,
    path: Path = DEFAULT_REGISTRY,
) -> Certificate:
    """Resolve a backend's eligibility for a run against this tree.

    A certificate earned under different component hashes is reported ``stale``
    rather than honoured. Carrying it forward would let a backend certified
    against an older schema registry appear admissible on a benchmark it has
    never actually satisfied.
    """
    registry = load_registry(path)
    certificate = registry.get(backend_name)
    if certificate is None:
        return Certificate(
            backend=backend_name,
            status=Status.UNTESTED,
            issued="",
            components=components,
            note="no certificate on file; run `callbench certify --model <id>`",
        )
    relevant = {"schemas", "taxonomy", "verifier"}
    drifted = [
        key
        for key in sorted(relevant)
        if certificate.components.get(key) != components.get(key)
    ]
    if drifted:
        return Certificate(
            backend=backend_name,
            status=Status.STALE,
            issued=certificate.issued,
            components=certificate.components,
            failures=certificate.failures,
            note=f"certified against a different tree; {', '.join(drifted)} moved since",
        )
    return certificate


def admissibility(report_systems: list[str], certificate: Certificate) -> dict[str, Any]:
    """The block a report carries so a reader knows whether to trust the table."""
    return {
        "backend": certificate.backend,
        "status": certificate.status,
        "admissible": certificate.admissible,
        "issued": certificate.issued,
        "failures": certificate.failures,
        "note": certificate.note,
        "systems": report_systems,
    }
