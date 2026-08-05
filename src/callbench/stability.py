"""Behavioural Replay Verification.

**Definition.** Two simulator implementations are *behaviourally equivalent*
if, over the canonical fixture suite and the canonical operation script, they
produce identical observable state transitions — the same before/after state
hashes and the same changed-resource sets at every step — regardless of any
difference in their implementation.

**Behavioural Stability (BS)** quantifies it::

    BS = identical observable transitions / total replay fixtures

with a target of **BS = 100% across implementation refactors**.

Why this and not source hashing
===============================

Hashing source declares every refactor a new benchmark, so nobody ever claims
reproducibility and the check becomes decorative. Hashing *behaviour* asserts
the property that actually matters: a rename, a re-factoring of the store, a
faster diff algorithm — all leave BS at 100%. A "harmless" change that silently
alters one mailbox does not, and the failing fixture names itself.

The baseline is committed. That makes a behavioural change a **failing check**
rather than a silent renumbering of everyone's prior results: to move it you
must re-record it deliberately, and the diff shows exactly which transitions
moved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .simulator import build_fixture, invoke
from .simulator.store import MailboxStore, ToolError

DEFAULT_BASELINE = Path("docs/behavioural-baseline.json")
CANONICAL_TIME = "2026-08-05T09:00:00+00:00"

#: The canonical fixture suite. Deliberately spans all three generators —
#: standard, injected and dense — because a refactor can preserve one shape
#: while breaking another.
CANONICAL_FIXTURES: tuple[str, ...] = (
    "fixture_std_201",
    "fixture_std_242",
    "fixture_std_400",
    "fixture_inj_807",
    "fixture_inj_812",
    "fixture_str_1000",
    "fixture_str_1042",
    "fixture_std_600",
)


@dataclass(frozen=True)
class Step:
    """One canonical operation.

    Arguments are resolved against the fixture at replay time rather than
    hardcoded, so the script is meaningful on every mailbox in the suite.
    """

    tool: str
    resolve: str  # how to derive arguments: see _arguments
    note: str = ""


#: The canonical script. It touches every side-effect class the simulator has —
#: read, create, mutate, send, destructive — because a transition that is never
#: exercised is a transition the check cannot protect.
CANONICAL_SCRIPT: tuple[Step, ...] = (
    Step("search_messages", "search_all", "read: baseline listing"),
    Step("list_labels", "none", "read: label set"),
    Step("read_thread", "first_thread", "read: thread membership and externals"),
    Step("list_attachments", "first_message", "read: attachment listing"),
    Step("resolve_contact", "first_contact_first_name", "read: ambiguity signal"),
    Step("mark_read", "first_message_read", "mutate: label set"),
    Step("modify_labels", "first_message_label", "mutate: label add"),
    Step("create_draft", "draft_in_first_thread", "create: draft"),
    Step("reply_to_thread", "reply_first_thread", "send: thread append"),
    Step("archive_message", "first_message", "mutate: inbox removal"),
    Step("forward_message", "forward_first_message", "send: new thread"),
    Step("delete_message", "second_message", "destructive: trash"),
)


@dataclass
class Transition:
    step: int
    tool: str
    ok: bool
    before_hash: str
    after_hash: str
    changed_resources: list[str]
    error_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FixtureSignature:
    fixture: str
    signature: str
    transitions: list[Transition] = field(default_factory=list)

    def to_dict(self, *, with_transitions: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {"fixture": self.fixture, "signature": self.signature}
        if with_transitions:
            payload["transitions"] = [t.to_dict() for t in self.transitions]
        return payload


@dataclass
class StabilityReport:
    matched: int
    total: int
    drifted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return (100.0 * self.matched / self.total) if self.total else 0.0

    @property
    def stable(self) -> bool:
        return self.matched == self.total and not self.missing and not self.added

    def to_dict(self) -> dict[str, Any]:
        return {
            "behavioural_stability": round(self.score, 4),
            "matched": self.matched,
            "total": self.total,
            "drifted": self.drifted,
            "missing_from_baseline": self.missing,
            "absent_from_run": self.added,
            "stable": self.stable,
        }


# ---- argument resolution --------------------------------------------------


def _arguments(kind: str, store: MailboxStore) -> dict[str, Any] | None:
    """Derive this step's payload from the mailbox, or skip if inapplicable."""
    messages = sorted(store.messages)
    threads = sorted(store.threads)

    if kind == "none":
        return {}
    if kind == "search_all":
        return {"sort": "received_at_desc", "limit": 5}
    if kind == "first_thread":
        return {"thread_id": threads[0]} if threads else None
    if kind == "first_message":
        return {"message_id": messages[0]} if messages else None
    if kind == "second_message":
        return {"message_id": messages[1]} if len(messages) > 1 else None
    if kind == "first_message_read":
        return {"message_id": messages[0], "read": True} if messages else None
    if kind == "first_message_label":
        return {"message_id": messages[0], "add": ["REPLAY"]} if messages else None
    if kind == "first_contact_first_name":
        return {"name": store.contacts[0].name.split()[0]} if store.contacts else None
    if kind == "draft_in_first_thread":
        if not threads or not store.contacts:
            return None
        return {
            "to": [store.contacts[0].email],
            "subject": "Replay draft",
            "body": "Canonical replay body.",
            "thread_id": threads[0],
        }
    if kind == "reply_first_thread":
        return {"thread_id": threads[0], "body": "Canonical replay reply."} if threads else None
    if kind == "forward_first_message":
        if not messages or not store.contacts:
            return None
        return {"message_id": messages[0], "to": [store.contacts[0].email], "comment": "fyi"}
    raise KeyError(f"unknown resolver {kind!r}")


# ---- signatures -----------------------------------------------------------


def trace(fixture: str) -> FixtureSignature:
    """Run the canonical script and record every observable transition."""
    store = build_fixture(fixture)
    transitions: list[Transition] = []

    for index, step in enumerate(CANONICAL_SCRIPT):
        arguments = _arguments(step.resolve, store)
        if arguments is None:
            # A step that does not apply to this mailbox is recorded as such,
            # so "the fixture no longer has a second message" is itself drift.
            transitions.append(
                Transition(index, step.tool, False, store.state_hash(), store.state_hash(),
                           [], "inapplicable")
            )
            continue

        before = store.snapshot()
        before_hash = store.state_hash()
        try:
            invoke(store, step.tool, arguments, CANONICAL_TIME)
            ok, error_kind = True, None
        except ToolError as exc:
            ok, error_kind = False, exc.kind
        transitions.append(
            Transition(
                step=index,
                tool=step.tool,
                ok=ok,
                before_hash=before_hash,
                after_hash=store.state_hash(),
                changed_resources=MailboxStore.diff(before, store.snapshot()),
                error_kind=error_kind,
            )
        )

    digest = hashlib.sha256()
    for transition in transitions:
        digest.update(
            json.dumps(transition.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        )
    return FixtureSignature(fixture, f"sha256:{digest.hexdigest()[:32]}", transitions)


def record(fixtures: tuple[str, ...] = CANONICAL_FIXTURES) -> dict[str, str]:
    return {fixture: trace(fixture).signature for fixture in fixtures}


def compare(baseline: dict[str, str], current: dict[str, str]) -> StabilityReport:
    shared = sorted(baseline.keys() & current.keys())
    matched = sum(1 for f in shared if baseline[f] == current[f])
    return StabilityReport(
        matched=matched,
        total=len(shared) or len(baseline) or len(current),
        drifted=[f for f in shared if baseline[f] != current[f]],
        missing=sorted(current.keys() - baseline.keys()),
        added=sorted(baseline.keys() - current.keys()),
    )


def load_baseline(path: Path = DEFAULT_BASELINE) -> dict[str, str] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    signatures = payload.get("signatures")
    return dict(signatures) if isinstance(signatures, dict) else None


def write_baseline(path: Path = DEFAULT_BASELINE) -> dict[str, str]:
    signatures = record()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "definition": (
                    "Two simulator implementations are behaviourally equivalent if they "
                    "produce identical observable state transitions over the canonical "
                    "fixture suite and script, regardless of implementation differences."
                ),
                "script": [
                    {"tool": step.tool, "note": step.note} for step in CANONICAL_SCRIPT
                ],
                "fixtures": list(CANONICAL_FIXTURES),
                "signatures": signatures,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return signatures


def measure(path: Path = DEFAULT_BASELINE) -> StabilityReport | None:
    baseline = load_baseline(path)
    if baseline is None:
        return None
    return compare(baseline, record())
