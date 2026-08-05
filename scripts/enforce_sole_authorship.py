#!/usr/bin/env python3
"""Deterministic authorship-policy guard.

This repository is owned and authored by Frank Van Laarhoven. AI systems are
development instruments only: not authors, co-authors, contributors,
maintainers, copyright holders or owners.

The guard scans staged files, the proposed commit message, and the configured
Git identity, and refuses the commit if prohibited attribution appears. It is
deterministic and runs from a hook, so compliance does not depend on anyone
remembering.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROHIBITED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"co-authored-by:\s*(claude|chatgpt|copilot|gemini)", re.I),
    re.compile(r"generated-by:\s*", re.I),
    re.compile(r"assisted-by:\s*", re.I),
    re.compile(
        r"\b(generated|written|built|created)\s+(by|with)\s+"
        r"(claude|chatgpt|copilot|gemini|an?\s+ai)\b",
        re.I,
    ),
    re.compile(r"\bpowered\s+by\s+(claude|chatgpt|copilot|gemini)\b", re.I),
)

TEXT_SUFFIXES = {
    ".md", ".mdx", ".txt", ".rst",
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini",
    ".sh", ".bash", ".zsh",
    ".html", ".css", ".scss",
}

#: Paths exempt from the content scan because their whole purpose is to name
#: the prohibited strings. Exempting the guard from itself is necessary; the
#: policy document is exempt for the same reason.
SELF_REFERENTIAL = {
    "scripts/enforce_sole_authorship.py",
    "scripts/enforce_history_authorship.sh",
    ".github/workflows/sole-authorship.yml",
}


def staged_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def scan_text(label: str, text: str) -> list[str]:
    findings: list[str] = []
    for pattern in PROHIBITED_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"{label}: prohibited attribution: {match.group(0)!r}")
    return findings


def scan_staged_files() -> list[str]:
    findings: list[str] = []
    for path in staged_files():
        if path.as_posix() in SELF_REFERENTIAL:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(str(path), text))
    return findings


def scan_tree(root: Path) -> list[str]:
    """Scan the working tree — used by CI, where nothing is staged."""
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(root).as_posix()
        if relative in SELF_REFERENTIAL:
            continue
        if any(part in {".git", ".venv", ".claude", "dist", "node_modules"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(relative, text))
    return findings


def scan_commit_message(message_file: Path) -> list[str]:
    if not message_file.exists():
        return [f"Commit message file not found: {message_file}"]
    return scan_text("commit message", message_file.read_text(encoding="utf-8"))


def verify_git_identity(expected_name: str, expected_email: str | None) -> list[str]:
    findings: list[str] = []
    name = subprocess.run(
        ["git", "config", "user.name"], check=False, capture_output=True, text=True
    ).stdout.strip()
    email = subprocess.run(
        ["git", "config", "user.email"], check=False, capture_output=True, text=True
    ).stdout.strip()

    if name != expected_name:
        findings.append(f"Git user.name is {name!r}; expected {expected_name!r}.")
    if expected_email and email != expected_email:
        findings.append(f"Git user.email is {email!r}; expected {expected_email!r}.")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit-message-file", type=Path)
    parser.add_argument("--expected-name", default="Frank Van Laarhoven")
    parser.add_argument("--expected-email")
    parser.add_argument(
        "--tree",
        type=Path,
        help="scan the whole working tree instead of the staged set (for CI)",
    )
    parser.add_argument(
        "--skip-identity",
        action="store_true",
        help="skip the Git identity check (CI checkouts have no local identity)",
    )
    args = parser.parse_args()

    findings = scan_tree(args.tree) if args.tree else scan_staged_files()
    if not args.skip_identity:
        findings.extend(verify_git_identity(args.expected_name, args.expected_email))
    if args.commit_message_file:
        findings.extend(scan_commit_message(args.commit_message_file))

    if findings:
        print("Authorship policy check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print("Authorship policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
