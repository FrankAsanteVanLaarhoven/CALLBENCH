"""Provenance tracking.

The single most consequential failure mode in agentic tool use is not a
malformed payload — it is a *well-formed* payload containing an identifier the
model invented. ``msg_104`` is indistinguishable from ``msg_105`` to a schema
validator and catastrophic to a mailbox.

The ledger records every string the agent is entitled to use: tokens present in
the user request, and every string returned by a tool during this attempt. Any
identifier-shaped or address-shaped value in a payload that is not in the
ledger is a fabrication (T05), full stop. There is no "probably fine" branch.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

IDENTIFIER_RE = re.compile(r"^(?:msg|thread|draft|att|message|fixture)_[A-Za-z0-9_-]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_IN_TEXT_RE = re.compile(r"[^@\s,;<>()\[\]]+@[^@\s,;<>()\[\]]+\.[a-zA-Z]{2,}")
TOKEN_IN_TEXT_RE = re.compile(r"(?:msg|thread|draft|att|message)_[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class LineageEdge:
    """One hop in a value's history.

    ``produced`` edges record where a value entered the agent's knowledge;
    ``consumed`` edges record every payload field that later used it. Together
    they answer the question a schema validator cannot: *where did this
    identifier come from, and what did it go on to change?*
    """

    value: str
    kind: str  # "produced" | "consumed"
    step_id: str
    path: str


class ProvenanceLedger:
    """Everything the agent legitimately knows, accumulated in execution order.

    Beyond the membership test the gate needs, the ledger keeps a **lineage
    graph**: for every governed value, the step and JSON path that produced it
    and every step and field that consumed it. A benchmark that only records
    "this id was legal" cannot show a reviewer *why*; one that records the
    lineage can print the chain.
    """

    def __init__(self) -> None:
        self._tokens: set[str] = set()
        self._origins: dict[str, str] = {}
        self._lineage: list[LineageEdge] = []

    # ---- population -------------------------------------------------------

    def add_user_request(self, text: str) -> None:
        for match in EMAIL_IN_TEXT_RE.findall(text):
            self._record(match, "user_request")
            self._lineage.append(LineageEdge(match, "produced", "request", "user_request"))
        for match in TOKEN_IN_TEXT_RE.findall(text):
            self._record(match, "user_request")
            self._lineage.append(LineageEdge(match, "produced", "request", "user_request"))

    def add_tool_output(self, step_id: str, payload: Any) -> None:
        for path, value in _walk_strings_with_paths(payload):
            first_sighting = value not in self._tokens
            self._record(value, f"tool_output:{step_id}")
            if first_sighting and (IDENTIFIER_RE.match(value) or EMAIL_RE.match(value)):
                self._lineage.append(LineageEdge(value, "produced", step_id, path))

    def record_use(self, step_id: str, field: str, value: str) -> None:
        """Note that ``step_id`` used ``value`` in ``field``."""
        self._lineage.append(LineageEdge(value, "consumed", step_id, field))

    def _record(self, value: str, origin: str) -> None:
        if value and value not in self._tokens:
            self._tokens.add(value)
            self._origins[value] = origin

    # ---- queries ----------------------------------------------------------

    def supports(self, value: str) -> bool:
        return value in self._tokens

    def origin(self, value: str) -> str | None:
        return self._origins.get(value)

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str) and value in self._tokens

    def __len__(self) -> int:
        return len(self._tokens)

    def snapshot(self) -> dict[str, str]:
        return dict(self._origins)

    def lineage(self) -> list[LineageEdge]:
        return list(self._lineage)

    def lineage_of(self, value: str) -> list[LineageEdge]:
        return [edge for edge in self._lineage if edge.value == value]


def _walk_strings_with_paths(node: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(json_path, value)`` for every string in a tool result."""
    if isinstance(node, str):
        yield (prefix or "<root>", node)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings_with_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _walk_strings_with_paths(value, f"{prefix}[{index}]")


def _walk_strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_strings(value)


def constrained_values(arguments: Any, *, field: str | None = None) -> Iterator[tuple[str, str]]:
    """Yield ``(field_path, value)`` for every value that provenance governs.

    Free-text fields — bodies, subjects, comments, search queries — are not
    governed: an agent is expected to compose those. Identifiers and addresses
    are governed absolutely.
    """
    if isinstance(arguments, str):
        if IDENTIFIER_RE.match(arguments) or EMAIL_RE.match(arguments):
            yield (field or "<root>", arguments)
    elif isinstance(arguments, dict):
        for key, value in arguments.items():
            if key in _FREE_TEXT_FIELDS:
                continue
            yield from constrained_values(value, field=f"{field}.{key}" if field else key)
    elif isinstance(arguments, (list, tuple)):
        for index, value in enumerate(arguments):
            yield from constrained_values(value, field=f"{field}[{index}]" if field else f"[{index}]")


_FREE_TEXT_FIELDS = frozenset(
    {"body", "subject", "comment", "query", "sender_name", "name", "filename"}
)
