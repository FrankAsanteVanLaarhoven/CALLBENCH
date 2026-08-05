"""Deferred reference resolution.

A plan step may carry ``"$s1.results[0].thread_id"`` in place of a value it
cannot know yet. The reference is resolved only after step ``s1`` has actually
returned, which is what structurally prevents the planner from guessing an
identifier: there is no syntax for "the id I think it will be".

A reference to a step that has not run — or to a path that does not exist in
what that step returned — is a missing dependency (T10), not a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REFERENCE_RE = re.compile(r"^\$(?P<step>[A-Za-z0-9_]+)(?P<path>(?:\.[A-Za-z0-9_]+|\[\d+\])*)$")
_SEGMENT_RE = re.compile(r"\.([A-Za-z0-9_]+)|\[(\d+)\]")


class UnresolvedReference(Exception):
    def __init__(self, reference: str, reason: str) -> None:
        super().__init__(f"{reference}: {reason}")
        self.reference = reference
        self.reason = reason


@dataclass(frozen=True)
class Reference:
    raw: str
    step_id: str
    path: tuple[str | int, ...]


def parse(value: Any) -> Reference | None:
    if not isinstance(value, str) or not value.startswith("$"):
        return None
    match = REFERENCE_RE.match(value)
    if match is None:
        raise UnresolvedReference(value, "malformed reference syntax")
    path: list[str | int] = []
    for key, index in _SEGMENT_RE.findall(match.group("path")):
        path.append(int(index) if index else key)
    return Reference(raw=value, step_id=match.group("step"), path=tuple(path))


def referenced_steps(arguments: Any) -> set[str]:
    found: set[str] = set()
    for ref in _iter_references(arguments):
        found.add(ref.step_id)
    return found


def resolve(arguments: Any, results: dict[str, Any]) -> Any:
    """Substitute every reference in ``arguments`` using prior step results."""
    ref = parse(arguments) if isinstance(arguments, str) else None
    if ref is not None:
        return _lookup(ref, results)
    if isinstance(arguments, dict):
        return {key: resolve(value, results) for key, value in arguments.items()}
    if isinstance(arguments, list):
        return [resolve(value, results) for value in arguments]
    return arguments


def _lookup(ref: Reference, results: dict[str, Any]) -> Any:
    if ref.step_id not in results:
        raise UnresolvedReference(ref.raw, f"step {ref.step_id} has not produced a result")
    node: Any = results[ref.step_id]
    for segment in ref.path:
        try:
            node = node[segment]
        except (KeyError, IndexError, TypeError) as exc:
            raise UnresolvedReference(ref.raw, f"no value at {segment!r}") from exc
    return node


def _iter_references(node: Any):  # type: ignore[no-untyped-def]
    if isinstance(node, str):
        try:
            ref = parse(node)
        except UnresolvedReference:
            return
        if ref is not None:
            yield ref
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_references(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_references(value)
