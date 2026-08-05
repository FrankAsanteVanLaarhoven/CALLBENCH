"""Tool schema registry and validation."""

from __future__ import annotations

from typing import Any

from .tools import CANONICAL_TOOLS, CATALOGUES, Catalogue, ToolSpec, get_catalogue

__all__ = [
    "CANONICAL_TOOLS",
    "CATALOGUES",
    "Catalogue",
    "ToolSpec",
    "get_catalogue",
    "schema_errors",
]


def schema_errors(
    tool: str, payload: dict[str, Any], catalogue: str | Catalogue
) -> list[tuple[str, str]]:
    """Validate a payload, returning ``(taxonomy_code, message)`` pairs.

    Accepts a catalogue by name or by object. The object form is what lets
    mutation testing validate against a catalogue that was never registered.

    Errors are mapped onto the taxonomy here rather than at the call site so
    that "missing required" and "undeclared property" stay distinguishable in
    the metrics, which is the whole point of splitting T02 from T04.
    """
    resolved = get_catalogue(catalogue) if isinstance(catalogue, str) else catalogue
    validator = resolved.validator(tool)
    found: list[tuple[str, str]] = []
    for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.path) or "<root>"
        message = f"{path}: {error.message}"
        if error.validator == "required":
            found.append(("T02_MISSING_REQUIRED_ARGUMENT", message))
        elif error.validator == "additionalProperties":
            found.append(("T04_UNDECLARED_ARGUMENT", message))
        else:
            found.append(("T03_INVALID_ARGUMENT_TYPE", message))
    return found
