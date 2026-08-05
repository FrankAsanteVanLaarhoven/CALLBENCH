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
from typing import Any

IDENTIFIER_RE = re.compile(r"^(?:msg|thread|draft|att|message|fixture)_[A-Za-z0-9_-]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_IN_TEXT_RE = re.compile(r"[^@\s,;<>()\[\]]+@[^@\s,;<>()\[\]]+\.[a-zA-Z]{2,}")
TOKEN_IN_TEXT_RE = re.compile(r"(?:msg|thread|draft|att|message)_[A-Za-z0-9_-]+")


class ProvenanceLedger:
    """Everything the agent legitimately knows, accumulated in execution order."""

    def __init__(self) -> None:
        self._tokens: set[str] = set()
        self._origins: dict[str, str] = {}

    # ---- population -------------------------------------------------------

    def add_user_request(self, text: str) -> None:
        for match in EMAIL_IN_TEXT_RE.findall(text):
            self._record(match, "user_request")
        for match in TOKEN_IN_TEXT_RE.findall(text):
            self._record(match, "user_request")

    def add_tool_output(self, step_id: str, payload: Any) -> None:
        for value in _walk_strings(payload):
            self._record(value, f"tool_output:{step_id}")

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
