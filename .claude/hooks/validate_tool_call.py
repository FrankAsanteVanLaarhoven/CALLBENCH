#!/usr/bin/env python3
"""PreToolUse hook: schema-validate calls into the simulated mailbox.

Schema validation must be deterministic rather than dependent on the model
deciding to check its own work, so it runs as a lifecycle hook rather than as
an instruction. This mirrors the in-process policy gate exactly — both call
``callbench.schemas.schema_errors`` — so the hook path and the harness path
cannot drift apart.

Calls that do not target the simulator are allowed through untouched: this hook
has one job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

MCP_PREFIX = "mcp__callbench__"


def fail(message: str) -> None:
    print(json.dumps({"decision": "block", "reason": message}))
    raise SystemExit(2)


def main() -> None:
    try:
        event: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError:
        fail("hook received malformed event JSON")
        return

    tool_name = str(event.get("tool_name", ""))
    if not tool_name.startswith(MCP_PREFIX):
        print(json.dumps({"decision": "allow"}))
        return

    try:
        from callbench.schemas import get_catalogue, schema_errors
    except ImportError as exc:
        fail(f"cannot import the schema registry: {exc}")
        return

    catalogue_name = str(event.get("catalogue", "catalogue_v1"))
    bare_name = tool_name[len(MCP_PREFIX):]
    catalogue = get_catalogue(catalogue_name)

    if bare_name not in catalogue:
        fail(f"unknown tool {bare_name!r} for {catalogue_name}")
        return

    errors = schema_errors(bare_name, event.get("tool_input", {}) or {}, catalogue_name)
    if errors:
        fail("; ".join(f"[{code}] {message}" for code, message in errors))
        return

    print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()
