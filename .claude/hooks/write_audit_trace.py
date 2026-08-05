#!/usr/bin/env python3
"""PostToolUse hook: append an audit line for every tool call.

An evaluation ledger that only records what the harness chose to record is not
an audit trail. This hook writes one line per call, outside the harness, so
that a disagreement between the harness's own trace and what actually happened
is detectable rather than invisible.

The hook is append-only, never blocks, and never fails the calling tool: a
broken audit sink must not become a broken run.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIT_PATH = Path(os.environ.get("CALLBENCH_AUDIT_LOG", "reports/audit.jsonl"))
MAX_FIELD_CHARS = 2000


def _truncate(value: object) -> object:
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) <= MAX_FIELD_CHARS:
        return value
    return {"_truncated": True, "chars": len(text), "head": text[:MAX_FIELD_CHARS]}


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    record = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tool": event.get("tool_name"),
        "input": _truncate(event.get("tool_input")),
        "ok": event.get("tool_response", {}).get("success", None)
        if isinstance(event.get("tool_response"), dict)
        else None,
        "session": event.get("session_id"),
    }

    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        # Auditing is best effort by design; it must never break the tool call.
        pass


if __name__ == "__main__":
    main()
