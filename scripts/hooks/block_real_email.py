#!/usr/bin/env python3
"""PreToolUse hook: refuse any operation that could reach a real mailbox.

The benchmark's central safety claim is that no run can produce a real-world
side effect. A claim enforced by convention is not enforced. This hook is the
deterministic backstop: it runs before every tool call, it does not consult a
model, and it blocks on match rather than warning.

Exit codes follow the pre-tool hook contract: 0 allows, 2 blocks.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

#: Tool names that denote a real provider, whatever the surrounding config says.
REAL_EMAIL_TOOLS = {
    "gmail.send", "gmail.reply", "gmail.forward", "gmail.delete",
    "outlook.send", "outlook.reply", "outlook.forward", "outlook.delete",
    "smtp.send", "imap.delete", "sendgrid.send", "ses.send_email",
}

#: Shell patterns that transmit mail or install a transport capable of it.
SHELL_PATTERNS = (
    r"\bsendmail\b",
    r"\bmsmtp\b",
    r"\bmutt\b\s+-s",
    r"\bmail\b\s+-s\s",
    r"\bswaks\b",
    r"curl[^\n|;]*gmail\.googleapis\.com",
    r"curl[^\n|;]*graph\.microsoft\.com/[^\s]*sendMail",
    r"curl[^\n|;]*api\.sendgrid\.com",
    r"smtplib\.SMTP\s*\(",
)

_SHELL_RE = re.compile("|".join(SHELL_PATTERNS), re.IGNORECASE)


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    raise SystemExit(2)


def main() -> None:
    try:
        event: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError:
        # A hook that cannot parse its input must not silently allow the call.
        block("hook received malformed event JSON")
        return

    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input", {}) or {}

    if tool_name in REAL_EMAIL_TOOLS:
        block(f"{tool_name} targets a real mail provider; benchmark mode is simulation-only")

    command = str(tool_input.get("command", ""))
    if command and _SHELL_RE.search(command):
        block(
            "this command can transmit real email. CallBench-Email runs entirely "
            "against the local simulator; if you need a controlled test inbox, read "
            "docs/METHODOLOGY.md first."
        )

    print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()
