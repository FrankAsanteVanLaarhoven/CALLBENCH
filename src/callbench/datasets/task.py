"""Benchmark task and execution oracle.

An oracle is deterministic and fixture-derived. It is written by the generator
from ground truth it computed itself — never by a model, and never by
inspecting an agent's output. That is the property that makes the benchmark an
evaluation rather than a consensus.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..contracts import Policy, TaskContract


@dataclass(frozen=True)
class Oracle:
    """Ground truth for one task."""

    decision: str = "execute"
    #: Canonical tool names that must appear, in this relative order, as a
    #: subsequence of the executed chain.
    required_tools: tuple[str, ...] = ()
    #: Canonical tool names that must never be invoked.
    forbidden_tools: tuple[str, ...] = ()
    #: Glob patterns over resource paths. Every observed change must match one.
    expected_changed_resources: tuple[str, ...] = ()
    #: State predicates evaluated against the final mailbox.
    predicates: tuple[dict[str, Any], ...] = ()
    required_recipients: tuple[str, ...] = ()
    forbidden_recipients: tuple[str, ...] = ()
    required_recipient_exclusion: tuple[str, ...] = ()
    #: Slack over ``len(required_tools)`` before T15 fires.
    max_extra_calls: int = 2

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Oracle:
        return Oracle(
            decision=raw.get("decision", "execute"),
            required_tools=tuple(raw.get("required_tools", ())),
            forbidden_tools=tuple(raw.get("forbidden_tools", ())),
            expected_changed_resources=tuple(raw.get("expected_changed_resources", ())),
            predicates=tuple(raw.get("predicates", ())),
            required_recipients=tuple(raw.get("required_recipients", ())),
            forbidden_recipients=tuple(raw.get("forbidden_recipients", ())),
            required_recipient_exclusion=tuple(raw.get("required_recipient_exclusion", ())),
            max_extra_calls=int(raw.get("max_extra_calls", 2)),
        )


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    #: The evaluation split: public | validation | hidden | adversarial | stress.
    #: Splits decide *who may see* a task.
    partition: str
    #: The difficulty stratum: easy | medium | hard | adversarial | stress.
    #: Tiers decide *what a task is*, and are reported within every split so a
    #: mixed split can still be broken down by difficulty.
    tier: str
    catalogue: str
    fixture: str
    current_time: str
    oracle: Oracle
    difficulty_factors: tuple[str, ...] = ()
    policy: dict[str, Any] = field(default_factory=dict)

    @property
    def split(self) -> str:
        return self.partition

    def contract(self) -> TaskContract:
        return TaskContract(
            task_id=self.id,
            user_request=self.prompt,
            catalogue=self.catalogue,
            fixture=self.fixture,
            current_time=self.current_time,
            policy=Policy.from_dict(self.policy),
            partition=self.partition,
            tier=self.tier,
            difficulty_factors=self.difficulty_factors,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["oracle"] = {k: _listify(v) for k, v in asdict(self.oracle).items()}
        payload["difficulty_factors"] = list(self.difficulty_factors)
        return payload

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Task:
        return Task(
            id=raw["id"],
            prompt=raw["prompt"],
            partition=raw["partition"],
            tier=raw.get("tier", raw["partition"]),
            catalogue=raw["catalogue"],
            fixture=raw["fixture"],
            current_time=raw["current_time"],
            oracle=Oracle.from_dict(raw.get("oracle", {})),
            difficulty_factors=tuple(raw.get("difficulty_factors", ())),
            policy=raw.get("policy", {}),
        )


def _listify(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_listify(v) for v in value]
    return value


def write_jsonl(tasks: list[Task], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Task]:
    with path.open(encoding="utf-8") as handle:
        return [Task.from_dict(json.loads(line)) for line in handle if line.strip()]


def iter_partitions(root: Path, partitions: list[str]) -> Iterator[tuple[str, list[Task]]]:
    for name in partitions:
        path = root / name / "tasks.jsonl"
        if not path.exists():
            continue
        yield name, read_jsonl(path)
