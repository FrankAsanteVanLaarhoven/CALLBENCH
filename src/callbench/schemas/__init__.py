"""Tool schema registry and validation."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from .tools import CANONICAL_TOOLS, CATALOGUES, Catalogue, ToolSpec, get_catalogue

__all__ = [
    "CANONICAL_TOOLS",
    "CATALOGUES",
    "Catalogue",
    "ToolSpec",
    "get_catalogue",
    "schema_errors",
]


@lru_cache(maxsize=64)
def _validator(schema_key: str, catalogue: str) -> Draft202012Validator:
    spec = get_catalogue(catalogue).spec(schema_key)
    return Draft202012Validator(spec.input_schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def schema_errors(tool: str, payload: dict[str, Any], catalogue: str) -> list[tuple[str, str]]:
    """Validate a payload, returning ``(taxonomy_code, message)`` pairs.

    Errors are mapped onto the taxonomy here rather than at the call site so
    that "missing required" and "undeclared property" stay distinguishable in
    the metrics, which is the whole point of splitting T02 from T04.
    """
    validator = _validator(tool, catalogue)
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
